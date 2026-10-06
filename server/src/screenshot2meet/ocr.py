"""
OCR engines behind one small interface: PaddleOCR (default) or Tesseract.

Both return a flat list of OcrWord (text, score 0..1, axis-aligned box).
Engines are cached per (engine, languages) because model loading is slow.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import numpy as np

# PaddleX checks model-host connectivity on every start; skip it (models are cached locally).
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


@dataclass(frozen=True)
class OcrWord:
    """One recognised text line."""

    text: str
    score: float
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def cx(self) -> float:
        """
        Horizontal centre of the box.

        Returns:
            float: x centre in pixels.
        """
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        """
        Vertical centre of the box.

        Returns:
            float: y centre in pixels.
        """
        return (self.y0 + self.y1) / 2


class OcrEngine(Protocol):
    """Anything that can read text lines from a BGR image."""

    def read(self, image: np.ndarray) -> list[OcrWord]:
        """
        Recognise text in an image.

        Args:
            image (np.ndarray): BGR image (H, W, 3), uint8.

        Returns:
            list[OcrWord]: Recognised lines with boxes in image coordinates.
        """
        ...


# Setting language code -> PaddleOCR `lang`. One "ch" model reads Simplified,
# Traditional Chinese and English; Latin-script languages share the latin model.
PADDLE_LANG = {"en": "ch", "ch": "ch", "chinese_cht": "ch", "zh": "ch", "zh_hant": "ch", "es": "es"}
TESSERACT_LANG = {"en": "eng", "ch": "chi_sim", "zh": "chi_sim", "chinese_cht": "chi_tra", "zh_hant": "chi_tra", "es": "spa"}


class PaddleEngine:
    """PaddleOCR 3.x pipeline(s); the first language's model reads the whole image."""

    def __init__(self, languages: tuple[str, ...]) -> None:
        """
        Load one PaddleOCR pipeline per distinct model needed by `languages`.

        Args:
            languages (tuple[str, ...]): Setting language codes, e.g. ("en", "ch", "es").
        """
        from paddleocr import PaddleOCR

        models: list[str] = []
        for lang in languages:
            model_lang = PADDLE_LANG.get(lang, lang)
            if model_lang not in models:
                models.append(model_lang)
        self.pipelines = [
            PaddleOCR(
                lang=model_lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            for model_lang in models or ["ch"]
        ]

    def read(self, image: np.ndarray) -> list[OcrWord]:
        """
        Recognise text with the primary model.

        Args:
            image (np.ndarray): BGR image.

        Returns:
            list[OcrWord]: Recognised lines.
        """
        return self._run(self.pipelines[0], image)

    def read_extra(self, image: np.ndarray) -> list[OcrWord]:
        """
        Recognise text with the secondary language models (e.g. Spanish), if any.

        Args:
            image (np.ndarray): BGR image (usually a small crop such as the header row).

        Returns:
            list[OcrWord]: Recognised lines from every secondary model.
        """
        words: list[OcrWord] = []
        for pipeline in self.pipelines[1:]:
            words.extend(self._run(pipeline, image))
        return words

    @staticmethod
    def _run(pipeline: object, image: np.ndarray) -> list[OcrWord]:
        """
        Run one PaddleOCR pipeline and convert its output.

        Args:
            pipeline (object): A paddleocr.PaddleOCR instance.
            image (np.ndarray): BGR image.

        Returns:
            list[OcrWord]: Recognised lines.
        """
        result = pipeline.predict(image)[0]  # type: ignore[attr-defined]
        return [
            OcrWord(text, float(score), *map(int, box))
            for text, score, box in zip(result["rec_texts"], result["rec_scores"], result["rec_boxes"])
            if text.strip()
        ]


class TesseractEngine:
    """Tesseract via pytesseract; dark images are inverted first."""

    def __init__(self, languages: tuple[str, ...]) -> None:
        """
        Configure languages and check that Tesseract is installed.

        Args:
            languages (tuple[str, ...]): Setting language codes.

        Raises:
            RuntimeError: If pytesseract or the tesseract binary is missing.
        """
        try:
            import pytesseract
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("pytesseract is not installed: pip install -e 'server[tesseract]'") from exc
        self.pytesseract = pytesseract
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:  # pragma: no cover - depends on system install
            raise RuntimeError("The tesseract binary was not found on PATH.") from exc
        self.lang = "+".join(dict.fromkeys(TESSERACT_LANG.get(lang, lang) for lang in languages)) or "eng"

    def read(self, image: np.ndarray) -> list[OcrWord]:
        """
        Recognise text lines with Tesseract.

        Args:
            image (np.ndarray): BGR image.

        Returns:
            list[OcrWord]: One entry per Tesseract line, with mean word confidence.
        """
        gray = image.mean(axis=2).astype(np.uint8)
        if gray.mean() < 110:
            gray = 255 - gray
        data = self.pytesseract.image_to_data(gray, lang=self.lang, output_type=self.pytesseract.Output.DICT)
        lines: dict[tuple[int, int, int], list[int]] = {}
        for i, text in enumerate(data["text"]):
            if text.strip() and float(data["conf"][i]) >= 0:
                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
                lines.setdefault(key, []).append(i)
        words: list[OcrWord] = []
        for indices in lines.values():
            x0 = min(data["left"][i] for i in indices)
            y0 = min(data["top"][i] for i in indices)
            x1 = max(data["left"][i] + data["width"][i] for i in indices)
            y1 = max(data["top"][i] + data["height"][i] for i in indices)
            text = " ".join(data["text"][i] for i in indices)
            score = float(np.mean([float(data["conf"][i]) for i in indices])) / 100
            words.append(OcrWord(text, score, x0, y0, x1, y1))
        return words

    def read_extra(self, image: np.ndarray) -> list[OcrWord]:
        """
        Tesseract reads all languages at once, so there is no secondary pass.

        Args:
            image (np.ndarray): Ignored.

        Returns:
            list[OcrWord]: Always empty.
        """
        return []


@lru_cache(maxsize=4)
def get_engine(name: str, languages: tuple[str, ...]) -> PaddleEngine | TesseractEngine:
    """
    Get a cached OCR engine.

    Args:
        name (str): "paddle" or "tesseract".
        languages (tuple[str, ...]): Setting language codes.

    Returns:
        PaddleEngine | TesseractEngine: The engine.
    """
    if name == "tesseract":
        return TesseractEngine(languages)
    return PaddleEngine(languages)
