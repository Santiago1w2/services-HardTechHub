package com.api.v1.compatibilityservice.controller;

import com.api.v1.compatibilityservice.dto.CompatibilityRequest;
import com.api.v1.compatibilityservice.dto.CompatibilityResponse;
import com.api.v1.compatibilityservice.service.CompatibilityService;
import org.springframework.web.bind.annotation.PostMapping;
import com.api.v1.compatibilityservice.repository.CompatibilityRepository;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/compatibility")
public class CompatibilityController {

    private final CompatibilityService compatibilityService;

    private final CompatibilityRepository repository;

    public CompatibilityController(CompatibilityService compatibilityService, CompatibilityRepository repository) {
        this.repository = repository;
        this.compatibilityService = compatibilityService;
    }

    @PostMapping("/check")
    public CompatibilityResponse check(@RequestBody CompatibilityRequest request) {
        CompatibilityResponse response = compatibilityService.check(request);
        repository.save(request, response);
        return response;
    }
}