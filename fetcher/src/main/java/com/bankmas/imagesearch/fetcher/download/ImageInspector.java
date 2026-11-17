package com.bankmas.imagesearch.fetcher.download;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;
import javax.imageio.ImageIO;
import javax.imageio.ImageReader;
import javax.imageio.stream.ImageInputStream;
import javax.imageio.stream.MemoryCacheImageInputStream;

/**
 * Validates that bytes decode as a supported image and extracts the format and
 * pixel dimensions. Fully decoding the image catches truncated or corrupt files
 * that a header-only check would accept.
 */
public final class ImageInspector {

    private static final Map<String, String> EXTENSIONS = Map.of(
            "jpeg", "jpg",
            "png", "png",
            "gif", "gif",
            "bmp", "bmp");
    private static final Map<String, String> MIME_TYPES = Map.of(
            "jpeg", "image/jpeg",
            "png", "image/png",
            "gif", "image/gif",
            "bmp", "image/bmp");

    private ImageInspector() {
    }

    public record ImageInfo(String format, String extension, String mimeType, int width, int height) {
    }

    public static ImageInfo inspect(byte[] bytes, long maxPixels) {
        if (bytes == null || bytes.length < 12) {
            throw new DownloadFailedException("invalid_image");
        }
        // Memory-backed stream: never writes temporary files to disk.
        try (ImageInputStream stream = new MemoryCacheImageInputStream(new ByteArrayInputStream(bytes))) {
            Iterator<ImageReader> readers = ImageIO.getImageReaders(stream);
            if (!readers.hasNext()) {
                throw new DownloadFailedException("unsupported_format");
            }
            ImageReader reader = readers.next();
            try {
                reader.setInput(stream, true, true);
                String format = normalize(reader.getFormatName());
                if (!EXTENSIONS.containsKey(format)) {
                    throw new DownloadFailedException("unsupported_format");
                }
                int width = reader.getWidth(0);
                int height = reader.getHeight(0);
                if (width <= 0 || height <= 0) {
                    throw new DownloadFailedException("invalid_image");
                }
                if ((long) width * (long) height > maxPixels) {
                    throw new DownloadFailedException("image_too_large");
                }
                if (reader.read(0) == null) {
                    throw new DownloadFailedException("invalid_image");
                }
                return new ImageInfo(format, EXTENSIONS.get(format), MIME_TYPES.get(format), width, height);
            } finally {
                reader.dispose();
            }
        } catch (IOException e) {
            throw new DownloadFailedException("invalid_image", e);
        }
    }

    private static String normalize(String formatName) {
        if (formatName == null) {
            return "";
        }
        String value = formatName.toLowerCase(Locale.ROOT).trim();
        return value.equals("jpg") ? "jpeg" : value;
    }
}
