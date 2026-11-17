package com.bankmas.imagesearch.fetcher.config;

import com.bankmas.imagesearch.fetcher.download.HttpImageDownloader;
import com.bankmas.imagesearch.fetcher.download.ImageDownloader;
import com.bankmas.imagesearch.fetcher.download.UrlSafety;
import com.bankmas.imagesearch.fetcher.events.FetchEventHub;
import com.bankmas.imagesearch.fetcher.http.HttpTransport;
import com.bankmas.imagesearch.fetcher.http.JdkHttpTransport;
import com.bankmas.imagesearch.fetcher.job.ImageFetchCoordinator;
import com.bankmas.imagesearch.fetcher.job.InMemoryJobRegistry;
import com.bankmas.imagesearch.fetcher.job.JobIdGenerator;
import com.bankmas.imagesearch.fetcher.job.FetchJobService;
import com.bankmas.imagesearch.fetcher.manifest.ManifestStore;
import com.bankmas.imagesearch.fetcher.manifest.MinioManifestStore;
import com.bankmas.imagesearch.fetcher.manifest.RetryingManifestStore;
import com.bankmas.imagesearch.fetcher.openverse.HttpOpenverseClient;
import com.bankmas.imagesearch.fetcher.openverse.OpenverseClient;
import com.bankmas.imagesearch.fetcher.openverse.OpenverseTokenProvider;
import com.bankmas.imagesearch.fetcher.retry.FixedIntervalPacer;
import com.bankmas.imagesearch.fetcher.retry.RequestPacer;
import com.bankmas.imagesearch.fetcher.retry.RetryPolicy;
import com.bankmas.imagesearch.fetcher.retry.Sleeper;
import com.bankmas.imagesearch.fetcher.retry.ThreadSleeper;
import com.bankmas.imagesearch.fetcher.storage.MinioObjectStorage;
import com.bankmas.imagesearch.fetcher.storage.ObjectStorage;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.time.Clock;
import java.util.Random;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3ClientBuilder;

/**
 * Wires the fetcher. All external dependencies are constructor-injected, which
 * keeps every client unit-testable with fakes.
 */
@Configuration
public class AppConfig {

    @Bean
    public Clock clock() {
        return Clock.systemUTC();
    }

    /** Compact JSON for API responses and SSE payloads. */
    @Bean
    @Primary
    public ObjectMapper objectMapper() {
        return Json.mapper();
    }

    /** Human-readable JSON for fetch manifests stored in MinIO. */
    @Bean
    @Qualifier("manifestObjectMapper")
    public ObjectMapper manifestObjectMapper() {
        return Json.prettyMapper();
    }

    @Bean
    public HttpTransport httpTransport(FetcherProperties properties) {
        return new JdkHttpTransport(properties.http().connectTimeout());
    }

    @Bean
    public Sleeper sleeper() {
        return new ThreadSleeper();
    }

    @Bean
    public OpenverseClient openverseClient(
            HttpTransport transport,
            ObjectMapper objectMapper,
            FetcherProperties properties,
            Sleeper sleeper,
            Clock clock) {
        FetcherProperties.Retry retry = properties.retry();
        RetryPolicy retryPolicy = new RetryPolicy(
                retry.openverseMaxAttempts(),
                retry.baseDelay(),
                retry.maxDelay(),
                retry.jitterRatio(),
                retry.maxRetryAfter(),
                new Random(),
                sleeper);
        OpenverseTokenProvider tokenProvider =
                new OpenverseTokenProvider(transport, objectMapper, properties.openverse(), retryPolicy, clock);
        RequestPacer pacer = new FixedIntervalPacer(
                properties.openverse().minRequestInterval(), sleeper, System::nanoTime);
        return new HttpOpenverseClient(
                transport, objectMapper, properties.openverse(), retryPolicy, pacer, tokenProvider);
    }

    @Bean
    public ImageDownloader imageDownloader(
            HttpTransport transport, FetcherProperties properties, Sleeper sleeper) {
        FetcherProperties.Retry retry = properties.retry();
        RetryPolicy retryPolicy = new RetryPolicy(
                retry.downloadMaxAttempts(),
                retry.baseDelay(),
                retry.maxDelay(),
                retry.jitterRatio(),
                retry.maxRetryAfter(),
                new Random(),
                sleeper);
        UrlSafety urlSafety = UrlSafety.systemDefault(properties.download().allowPrivateAddresses());
        return new HttpImageDownloader(transport, properties.download(), retryPolicy, urlSafety);
    }

    @Bean
    public S3Client s3Client(FetcherProperties properties) {
        FetcherProperties.Storage storage = properties.storage();
        S3ClientBuilder builder = S3Client.builder()
                .endpointOverride(URI.create(storage.endpoint()))
                .region(Region.of(storage.region()))
                .forcePathStyle(storage.pathStyleAccess())
                .httpClientBuilder(UrlConnectionHttpClient.builder());
        if (storage.hasStaticCredentials()) {
            builder.credentialsProvider(StaticCredentialsProvider.create(
                    AwsBasicCredentials.create(storage.accessKey(), storage.secretKey())));
        } else {
            // Fall back to the standard AWS credential chain (env, profile, role)
            // instead of ever shipping a hardcoded secret.
            builder.credentialsProvider(DefaultCredentialsProvider.builder().build());
        }
        return builder.build();
    }

    @Bean
    public ObjectStorage objectStorage(S3Client s3Client, FetcherProperties properties) {
        return new MinioObjectStorage(s3Client, properties.storage());
    }

    @Bean
    public ManifestStore manifestStore(
            ObjectStorage objectStorage,
            @Qualifier("manifestObjectMapper") ObjectMapper objectMapper,
            FetcherProperties properties,
            Sleeper sleeper) {
        return new RetryingManifestStore(
                new MinioManifestStore(objectStorage, objectMapper),
                properties.jobs().manifestSaveAttempts(),
                properties.jobs().manifestRetryDelay(),
                sleeper);
    }

    @Bean
    public InMemoryJobRegistry jobRegistry(FetcherProperties properties) {
        return new InMemoryJobRegistry(properties.jobs().maxRetainedJobs());
    }

    @Bean
    public JobIdGenerator jobIdGenerator(Clock clock, InMemoryJobRegistry registry) {
        return new JobIdGenerator(clock, registry);
    }

    @Bean
    public ImageFetchCoordinator imageFetchCoordinator(
            OpenverseClient openverseClient,
            ImageDownloader imageDownloader,
            ObjectStorage objectStorage,
            FetcherProperties properties) {
        return new ImageFetchCoordinator(openverseClient, imageDownloader, objectStorage, properties);
    }

    @Bean(destroyMethod = "shutdownNow")
    @Qualifier("fetchJobExecutor")
    public ExecutorService fetchJobExecutor(FetcherProperties properties) {
        ThreadFactory threadFactory = daemonThreadFactory("fetch-job-");
        return new ThreadPoolExecutor(
                properties.jobs().workerThreads(),
                properties.jobs().workerThreads(),
                60L,
                TimeUnit.SECONDS,
                new LinkedBlockingQueue<>(properties.jobs().queueCapacity()),
                threadFactory,
                new ThreadPoolExecutor.AbortPolicy());
    }

    @Bean(destroyMethod = "shutdownNow")
    @Qualifier("sseDispatcher")
    public ExecutorService sseDispatcher() {
        return Executors.newFixedThreadPool(2, daemonThreadFactory("sse-dispatch-"));
    }

    @Bean
    public FetchEventHub fetchEventHub(
            ObjectMapper objectMapper,
            @Qualifier("sseDispatcher") ExecutorService sseDispatcher,
            FetcherProperties properties) {
        return new FetchEventHub(objectMapper, sseDispatcher, properties.events().timeout());
    }

    @Bean
    public FetchJobService fetchJobService(
            InMemoryJobRegistry registry,
            JobIdGenerator jobIdGenerator,
            @Qualifier("fetchJobExecutor") ExecutorService fetchJobExecutor,
            ImageFetchCoordinator coordinator,
            ManifestStore manifestStore,
            FetchEventHub eventHub,
            FetcherProperties properties,
            Clock clock) {
        return new FetchJobService(
                registry,
                jobIdGenerator,
                fetchJobExecutor,
                coordinator,
                manifestStore,
                eventHub,
                properties,
                clock);
    }

    private static ThreadFactory daemonThreadFactory(String prefix) {
        AtomicInteger counter = new AtomicInteger();
        return runnable -> {
            Thread thread = new Thread(runnable, prefix + counter.incrementAndGet());
            thread.setDaemon(true);
            return thread;
        };
    }
}
