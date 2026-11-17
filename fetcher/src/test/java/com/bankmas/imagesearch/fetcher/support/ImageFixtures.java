package com.bankmas.imagesearch.fetcher.support;

import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import javax.imageio.ImageIO;

public final class ImageFixtures {

    private ImageFixtures() {
    }

    public static byte[] png(int width, int height, Color color) {
        return encode("png", width, height, color);
    }

    public static byte[] jpeg(int width, int height, Color color) {
        return encode("jpg", width, height, color);
    }

    private static byte[] encode(String format, int width, int height, Color color) {
        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
        Graphics2D graphics = image.createGraphics();
        try {
            graphics.setColor(color);
            graphics.fillRect(0, 0, width, height);
        } finally {
            graphics.dispose();
        }
        try (ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            if (!ImageIO.write(image, format, out)) {
                throw new IllegalStateException("no ImageIO writer for " + format);
            }
            return out.toByteArray();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
