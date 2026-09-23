package com.api.v1.compatibilityservice.controller;

import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;
import org.springframework.dao.DataAccessException;

@RestController
public class HealthController {
    private final JdbcTemplate jdbc;
    public HealthController(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @GetMapping("/health")
    public Map<String, String> health() {
        try {
            jdbc.queryForList("SELECT id FROM compatibility_checks LIMIT 1");
        } catch (DataAccessException exception) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Compatibility database unavailable");
        }
        return Map.of("service", "compatibility-service", "status", "healthy");
    }
}
