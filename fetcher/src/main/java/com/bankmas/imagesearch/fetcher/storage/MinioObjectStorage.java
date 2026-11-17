package com.bankmas.imagesearch.fetcher.storage;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import java.util.Map;
import java.util.regex.Pattern;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.CreateBucketRequest;
import software.amazon.awssdk.services.s3.model.HeadBucketRequest;
import software.amazon.awssdk.services.s3.model.HeadObjectRequest;
import software.amazon.awssdk.services.s3.model.NoSuchBucketException;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.S3Exception;

/**
 * MinIO (S3-compatible) implementation. The application bucket is created on
 * first use so the fetcher works against a fresh MinIO instance without manual
 * setup, while startup itself never depends on MinIO being reachable.
 */
public final class MinioObjectStorage implements ObjectStorage {

    private static final Pattern BUCKET_NAME = Pattern.compile("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$");

    private final S3Client client;
    private final String bucket;
    private final Object bucketLock = new Object();
    private volatile boolean bucketReady;

    public MinioObjectStorage(S3Client client, FetcherProperties.Storage properties) {
        if (!BUCKET_NAME.matcher(properties.bucket()).matches()) {
            throw new IllegalArgumentException("invalid S3 bucket name: " + properties.bucket());
        }
        this.client = client;
        this.bucket = properties.bucket();
    }

    @Override
    public boolean exists(String key) {
        ensureBucket();
        try {
            client.headObject(HeadObjectRequest.builder().bucket(bucket).key(key).build());
            return true;
        } catch (NoSuchKeyException e) {
            return false;
        } catch (S3Exception e) {
            if (e.statusCode() == 404) {
                return false;
            }
            throw e;
        }
    }

    @Override
    public void put(String key, byte[] content, String contentType, Map<String, String> metadata) {
        ensureBucket();
        PutObjectRequest.Builder request = PutObjectRequest.builder()
                .bucket(bucket)
                .key(key)
                .contentType(contentType);
        if (metadata != null && !metadata.isEmpty()) {
            request.metadata(metadata);
        }
        client.putObject(request.build(), RequestBody.fromBytes(content));
    }

    private void ensureBucket() {
        if (bucketReady) {
            return;
        }
        synchronized (bucketLock) {
            if (bucketReady) {
                return;
            }
            try {
                client.headBucket(HeadBucketRequest.builder().bucket(bucket).build());
            } catch (NoSuchBucketException e) {
                createBucket();
            } catch (S3Exception e) {
                if (e.statusCode() != 404) {
                    throw e;
                }
                createBucket();
            }
            bucketReady = true;
        }
    }

    private void createBucket() {
        client.createBucket(CreateBucketRequest.builder().bucket(bucket).build());
    }
}
