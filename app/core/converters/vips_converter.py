"""Vips converter via in-process pyvips library."""

import time
from typing import Dict, Any, List, Optional, Union
from .base import BaseConverter
from ..telemetry import TelemetryMonitor
from ..logger import get_logger
from ..utils import get_pyvips, quality_to_jxl_distance

log = get_logger(__name__)


class VipsConverter(BaseConverter):
    """Convert still images via in-process pyvips library (libvips Python binding).

    Pure in-process conversion with no subprocess overhead. Supports webp, avif,
    and jxl via pyvips' webpsave, heifsave, and jxlsave methods.
    """

    def __init__(self):
        """Initialize Vips converter."""
        super().__init__()

    def get_name(self) -> str:
        """Return the converter name."""
        return "pyvips"

    def supported_formats(self) -> List[str]:
        """Return list of supported output formats."""
        return ["webp", "avif", "jxl"]

    def _convert_via_pyvips(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
    ) -> Dict[str, Any]:
        """In-process conversion via pyvips library.

        Loads the image, encodes it with format-specific save methods
        (webpsave, heifsave, jxlsave), and returns encoding metadata.

        Args:
            input_path: Input image path.
            output_path: Output file path.
            target_format: Output format ('webp', 'avif', or 'jxl').
            quality: Quality value (0-100; mapped to JXL distance if needed).

        Returns:
            Dict with 'method', 'quality', and format-specific metadata.

        Raises:
            ImportError: If pyvips is not available.
            ValueError: If format is unsupported.
        """
        vips = get_pyvips()
        if vips is None:
            raise ImportError("pyvips library not initialized. Check libvips installation.")

        # Fix #18: use default (random) access so heifsave/AVIF codecs
        # don't fail with "not a random access image".
        image = vips.Image.new_from_file(input_path)

        params: Dict[str, Any] = {}
        if target_format == "webp":
            log.debug(f"pyvips webpsave: Q={round(quality)} (frac={quality})")
            # Encoder needs an int scalar; record the fractional quality for analytics.
            image.webpsave(output_path, Q=round(quality))
            params = {"method": "webpsave", "Q": quality}
        elif target_format == "avif":
            log.debug(f"pyvips heifsave (AVIF): Q={round(quality)} (frac={quality})")
            # pyvips uses heifsave for AVIF when built with libheif/libaom
            image.heifsave(output_path, compression="av1", Q=round(quality))
            params = {"method": "heifsave", "compression": "av1", "Q": quality}
        elif target_format == "jxl":
            dist = float(quality_to_jxl_distance(quality))
            log.debug(f"pyvips jxlsave: distance={dist}")
            if quality >= 100:
                image.jxlsave(output_path, lossless=True, distance=0.0)
                params = {"method": "jxlsave", "lossless": True, "distance": 0.0}
            else:
                image.jxlsave(output_path, distance=dist)
                params = {"method": "jxlsave", "distance": dist}
        else:
            raise ValueError(f"Unsupported format: {target_format}")

        return params

    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        is_intermediate: bool = False,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Convert a single image via in-process pyvips.

        Args:
            input_path: Path to input image.
            output_path: Path where output should be written.
            target_format: Output format ('webp', 'avif', or 'jxl').
            quality: Quality value 0-100 (higher is better).
            is_intermediate: Unused.
            run_id: Optional batch run ID for telemetry.

        Returns:
            Dict with conversion result including success status, duration, telemetry,
            and error details.
        """
        self._set_active_run_id(run_id)
        return self._run_library(
            self._convert_via_pyvips,
            "pyvips",
            quality,
            input_path,
            output_path,
            target_format,
            quality,
            run_id=run_id,
            output_path=output_path
        )
