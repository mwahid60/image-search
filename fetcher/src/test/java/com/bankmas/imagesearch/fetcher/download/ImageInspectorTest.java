package com.bankmas.imagesearch.fetcher.download;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.bankmas.imagesearch.fetcher.support.ImageFixtures;
import java.awt.Color;
import java.util.Arrays;
import org.junit.jupiter.api.Test;

class ImageInspectorTest {

    @Test
    void readsPngFormatAndDimensions() {
        byte[] png = ImageFixtures.png(10, 20, Color.RED);

        ImageInspector.ImageInfo info = ImageInspector.inspect(png, 1_000_000);

        assertThat(info.extension()).isEqualTo("png");
        assertThat(info.mimeType()).isEqualTo("image/png");
        assertThat(info.width()).isEqualTo(10);
        assertThat(info.height()).isEqualTo(20);
    }

    @Test
    void normalizesJpegToJpgExtension() {
        byte[] jpeg = ImageFixtures.jpeg(32, 16, Color.BLUE);

        ImageInspector.ImageInfo info = ImageInspector.inspect(jpeg, 1_000_000);

        assertThat(info.format()).isEqualTo("jpeg");
        assertThat(info.extension()).isEqualTo("jpg");
        assertThat(info.mimeType()).isEqualTo("image/jpeg");
    }

    @Test
    void rejectsTruncatedImages() {
        byte[] png = ImageFixtures.png(64, 64, Color.GREEN);

        assertThatThrownBy(() -> ImageInspector.inspect(Arrays.copyOf(png, png.length / 3), 1_000_000))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("invalid_image");
    }

    @Test
    void rejectsUnknownFormats() {
        byte[] notAnImage = "this is definitely not an image file".getBytes();

        assertThatThrownBy(() -> ImageInspector.inspect(notAnImage, 1_000_000))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("unsupported_format");
    }

    @Test
    void rejectsImagesAbovePixelLimit() {
        byte[] png = ImageFixtures.png(100, 100, Color.MAGENTA);

        assertThatThrownBy(() -> ImageInspector.inspect(png, 5_000))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("image_too_large");
    }
}
