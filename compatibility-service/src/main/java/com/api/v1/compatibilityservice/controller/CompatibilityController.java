package com.api.v1.compatibilityservice.controller;

import com.api.v1.compatibilityservice.dto.CompatibilityRequest;
import com.api.v1.compatibilityservice.dto.CompatibilityResponse;
import com.api.v1.compatibilityservice.service.CompatibilityService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/compatibility")
public class CompatibilityController {

    private final CompatibilityService compatibilityService;

    public CompatibilityController( CompatibilityService compatibilityService) {
        this.compatibilityService = compatibilityService;
    }

    @PostMapping("/check")
    public CompatibilityResponse check(@RequestBody CompatibilityRequest request) {
        return compatibilityService.check(request);
    }
}