package com.api.v1.compatibilityservice;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

// Build-time tests supply their own configuration; Compose applies only at runtime.
@SpringBootTest(properties = {
    "SERVER_PORT=0",
    "spring.datasource.url=jdbc:h2:mem:compatibility",
    "spring.datasource.username=sa",
    "spring.datasource.password=",
    "CATALOG_SERVICE_URL=http://localhost:8002"
})
class CompatibilityServiceApplicationTests {

    @Test
    void contextLoads() {
    }

}
