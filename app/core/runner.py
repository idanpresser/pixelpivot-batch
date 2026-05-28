"""Runner — converter registry and unified image conversion interface.

Instantiates tool adapters (FFmpeg, ImageMagick, VIPS, Sharp) and provides
a strategy-pattern registry for delegating conversions to the right converter.
"""

import os
import sys
import shutil
from typing import Dict, Any, List, Optional, Union
from .converters.base import BaseConverter
from .converters.ffmpeg_converter import FFmpegConverter
from .converters.ffmpeg_nvenc_converter import FFmpegNvencConverter
from .converters.magick_converter import MagickConverter
from .converters.vips_converter import VipsConverter
from .converters.sharp_converter import SharpConverter
from .logger import get_logger
from . import paths

log = get_logger(__name__)


class Runner:
    """
    Registry for various image conversion adapters (Strategy Pattern).
    Manages tool instantiation and provides a unified interface.
    """

    def __init__(self, tools_config: Optional[Dict[str, str]] = None, sharp_port: Optional[int] = None):
        """Initialize the converter registry with tool paths and instances.

        Args:
            tools_config: Optional dict to override default tool paths.
            sharp_port: Optional port for the Sharp daemon socket connection.
        """
        root_dir = paths.PROJ_ROOT
        app_dir = paths.APP_ROOT

        def find_tool(name: str, local_subpath: str) -> str:
            which_path = shutil.which(name)
            if which_path:
                return which_path

            exe_name = f"{name}.exe" if sys.platform == "win32" else name
            tool_path = os.path.join(str(paths.TOOLS_DIR), local_subpath, exe_name)

            # If the expected fallback path exists, use it; otherwise return tool name (for PATH lookup at runtime).
            return tool_path if os.path.exists(tool_path) else name

        def find_magick() -> str:
            # 1. Prefer 'magick' (ImageMagick 7+)
            m = shutil.which("magick")
            if m: return m

            # 2. Check 'convert' (ImageMagick 6), but avoid Windows system 'convert.exe'
            c = shutil.which("convert")
            if c and "System32" not in c and "WINDOWS" not in c.upper():
                return c

            # 3. Check for bundled tool in TOOLS_DIR
            fallback = os.path.join(str(paths.TOOLS_DIR), "ImageMagick", "magick" + (".exe" if sys.platform == "win32" else ""))
            if os.path.exists(fallback):
                return fallback

            return "magick" # Fallback to name for last-ditch PATH search

        magick_cmd = find_magick()

        default_paths = {
            "ffmpeg": find_tool("ffmpeg", os.path.join("ffmpeg", "bin")),
            "magick": magick_cmd,
            "vips": find_tool("vips", os.path.join("vips", "bin")),
            "sharp_runner": os.path.join(app_dir, "scripts", "sharp_daemon.js"),
        }

        self.paths = default_paths
        if tools_config:
            self.paths.update(tools_config)

        log.debug(f"Initializing Runner with tool paths: {self.paths}")

        # Register converters
        self._converters: Dict[str, BaseConverter] = {
            "ffmpeg": FFmpegConverter(self.paths["ffmpeg"]),
            "ffmpeg_nvenc": FFmpegNvencConverter(self.paths["ffmpeg"]),
            "imagemagick": MagickConverter(self.paths["magick"]),
            "pyvips": VipsConverter(),
            "sharp": SharpConverter(port=sharp_port) if sharp_port else SharpConverter(),
        }

        log.debug(f"Registered converters: {list(self._converters.keys())}")

    def get_converter(self, name: str) -> Optional[BaseConverter]:
        """Retrieve a converter instance by name.

        Args:
            name: Converter name (e.g. "ffmpeg", "imagemagick", "sharp").

        Returns:
            BaseConverter instance, or None if not registered.
        """
        return self._converters.get(name)

    def list_converters(self) -> List[str]:
        """List all registered converter names.

        Returns:
            List of converter names available in the registry.
        """
        return list(self._converters.keys())

    def convert(
        self,
        tool_name: str,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        use_gpu: bool = False,
        is_intermediate: bool = True
    ) -> Dict[str, Any]:
        """Delegate a conversion to the specified tool adapter.

        Args:
            tool_name: Converter name (must be in list_converters()).
            input_path: Path to source image.
            output_path: Path for output image.
            target_format: Target format (e.g. "webp", "avif", "jxl").
            quality: Tool-native quality scalar.
            use_gpu: Whether to use GPU if available.
            is_intermediate: Whether this is a temporary intermediate file.

        Returns:
            Dict with conversion result (success, error, etc.).
        """
        converter = self.get_converter(tool_name)
        if not converter:
            error_msg = f"Converter '{tool_name}' not found. Available: {self.list_converters()}"
            log.error(error_msg)
            return {"success": False, "error": error_msg}

        log.debug(
            f"Delegating conversion to {tool_name}: {input_path} -> {output_path} (format={target_format}, quality={quality}, gpu={use_gpu})"
        )
        return converter.convert(
            input_path, 
            output_path, 
            target_format, 
            quality, 
            use_gpu=use_gpu, 
            is_intermediate=is_intermediate
        )
