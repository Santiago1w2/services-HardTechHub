package com.api.v1.compatibilityservice.client;

import com.api.v1.compatibilityservice.dto.ProductResponse;
import org.springframework.stereotype.Component;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.client.RestClient;

@Component
public class CatalogClient {

    private final RestClient restClient;

    public CatalogClient(RestClient restClient) {
        this.restClient = restClient;
    }

    public ProductResponse getProduct(Long productId) {

        try {
            return restClient
                .get()
                .uri("/api/products/{id}", productId)
                .retrieve()
                .body(ProductResponse.class);
        } catch (RestClientResponseException exc) {
            if (exc.getStatusCode().value() == 404) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Product not found");
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "Catalog Service error");
        } catch (RestClientException exc) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Catalog Service unavailable");
        }
    }
}