"""Cavif converter via command-line encoder subprocess."""

import os
from typing import List, Union, Optional
from .base import BaseConverter, ConvertResult

class CavifConverter(BaseConverter):
    """Convert still images to AVIF via the cavif CLI utility."""

    def __init__(self, cavif_path: str):
        """Initialize Cavif converter.

        Args:
            cavif_path: Path to the cavif binary.
        """
        super().__init__()
        self.cavif_path = cavif_path

    def get_name(self) -> str:
        """Return the converter name."""
        return "cavif"

    def supported_formats(self) -> List[str]:
        """Return list of supported output formats."""
        return ["avif"]

    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        is_intermediate: bool = False,
        run_id: Optional[int] = None,
    ) -> ConvertResult:
        """Convert a single image file via cavif subprocess.

        Args:
            input_path: Path to input image.
            output_path: Path where output should be written.
            target_format: Output format (must be 'avif').
            quality: Quality value (0-100).
            run_id: Optional batch run ID for telemetry.

        Returns:
            ConvertResult containing success status, duration, telemetry, parameters, error, and fatal status.
        """
        self._set_active_run_id(run_id)
        
        # Guard against format mismatch
        if target_format != "avif":
            raise ValueError(f"CavifConverter only supports 'avif' encoding; requested '{target_format}'")
            
        if self.is_broken and not getattr(self, "_bypass_breaker", False):
            return ConvertResult(success=False, error=f"{self.get_name()} is marked as broken")

        # Build cavif command: cavif --quality <quality> -o <out> <in>
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        quality_val = round(quality)
        cmd = [
            self.cavif_path,
            "--quality", str(quality_val),
            "-o", output_path,
            input_path
        ]
        
        return self._run_subprocess(
            cmd=cmd,
            tool_name="cavif",
            params=["--quality", str(quality_val)],
            quality=quality,
            run_id=run_id,
            output_path=output_path
        )
