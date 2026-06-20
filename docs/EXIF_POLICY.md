# EXIF Metadata Policy

**Policy: Preserve EXIF Metadata Everywhere**

To ensure high-fidelity image migration, PixelPivot Batch Engine preserves all EXIF metadata (including color profiles, camera information, GPS coordinates, and orientation tags) across all converters. This prevents issues like incorrect image rotation or washed-out colors.

## Implementation Details

1. **ImageMagick (`MagickConverter`)**:
   - Preserves EXIF metadata by default. No flags are passed that would strip metadata (like `-strip`).

2. **libvips (`VipsConverter`)**:
   - Preserves EXIF metadata by default. The `strip` flag is omitted or set to `False` in pyvips save methods.

3. **Sharp (`SharpConverter`)**:
   - Preserves EXIF metadata by calling `.withMetadata()` on the sharp pipeline instance inside the persistent Node.js daemon (`app/scripts/sharp_daemon.js`).

4. **FFmpeg (`FFmpegConverter`)**:
   - Preserves metadata by passing `-map_metadata 0` (for single conversions) and `-map_metadata <index>` (for multi-input/multi-output batch mappings) to map metadata from input files to their corresponding output files.
