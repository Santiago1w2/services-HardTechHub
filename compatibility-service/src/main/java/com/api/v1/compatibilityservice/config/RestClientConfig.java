package com.api.v1.compatibilityservice.config;


import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

@Configuration
public class RestClientConfig {

    @Value("${catalog.url}")
    private String catalogUrl;

    @Bean
    public RestClient restClient() {
        return RestClient.builder().baseUrl(catalogUrl).build();
    }
}