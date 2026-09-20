package com.api.v1.compatibilityservice.service;

import com.api.v1.compatibilityservice.client.CatalogClient;
import com.api.v1.compatibilityservice.dto.CompatibilityRequest;
import com.api.v1.compatibilityservice.dto.CompatibilityResponse;
import com.api.v1.compatibilityservice.dto.ComponentRequest;
import com.api.v1.compatibilityservice.dto.ProductResponse;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;

@Service
public class CompatibilityService {

    private final CatalogClient catalogClient;

    public CompatibilityService(CatalogClient catalogClient) {
        this.catalogClient = catalogClient;
    }

    public CompatibilityResponse check(CompatibilityRequest request) {

        ProductResponse cpu = null;
        ProductResponse motherboard = null;
        ProductResponse ram = null;
        ProductResponse gpu = null;
        ProductResponse psu = null;

        if (request == null || request.getComponents() == null || request.getComponents().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Components are required");
        }
        Set<String> seen = new HashSet<>();
        for (ComponentRequest component : request.getComponents()) {
            if (component == null || component.getType() == null || component.getProduct_id() == null
                    || component.getProduct_id() <= 0) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid component");
            }
            String type = component.getType().toLowerCase(Locale.ROOT);
            if (!Set.of("cpu", "motherboard", "ram", "gpu", "psu").contains(type) || !seen.add(type)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Unknown or duplicate component type");
            }
            ProductResponse product =catalogClient.getProduct(component.getProduct_id());
            if (product == null || !Boolean.TRUE.equals(product.getIs_active()) || product.getSpecs() == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Inactive product or missing specifications");
            }
            switch (type) {
                case "cpu" -> cpu = product;
                case "motherboard" -> motherboard = product;
                case "ram" -> ram = product;
                case "gpu" -> gpu = product;
                case "psu" -> psu = product;
            }
        }

        List<String> messages = new ArrayList<>();

        boolean compatible = true;


        if (cpu != null && motherboard != null) {

            String cpuSocket =
                    textSpec(cpu, "socket");

            String motherboardSocket =
                    textSpec(motherboard, "socket");

            if (cpuSocket.equals(motherboardSocket)) {
                messages.add("CPU y Motherboard usan "+ cpuSocket + ".");

            } else {
                messages.add("La CPU usa "+ cpuSocket+ ", pero la Motherboard usa "+ motherboardSocket + ".");

                compatible = false;
            }
        }


        if (ram != null && motherboard != null) {
            String ramType =textSpec(ram, "memory_type");

            String motherboardRamType =textSpec(motherboard, "memory_type");

            if (ramType.equals(motherboardRamType)) {
                messages.add("RAM y Motherboard usan "+ ramType+ ".");
            } else {
                messages.add( "La RAM es "+ ramType + ", pero la Motherboard requiere " + motherboardRamType + ".");
                compatible = false;
            }
        }

        if (gpu != null && psu != null) {
            Number recommendedPsu = numberSpec(gpu, "recommended_psu_watts");
            Number psuWattage = numberSpec(psu, "wattage");
            if (recommendedPsu != null && psuWattage != null) {
                if (psuWattage.doubleValue() >= recommendedPsu.doubleValue()) {
                    messages.add("La fuente de " + psuWattage + " W cumple el requisito de "+ recommendedPsu+ " W de la GPU.");
                } else {
                    messages.add("La fuente de "+ psuWattage+ " W no alcanza los "+ recommendedPsu+ " W recomendados por la GPU.");
                    compatible = false;
                }
            }
        }

        if (messages.isEmpty()) {
            return new CompatibilityResponse(false, List.of("No hay suficientes componentes para evaluar compatibilidad."));
        }
        return new CompatibilityResponse(compatible, messages);
    }

    private String textSpec(ProductResponse product, String key) {
        Object value = product.getSpecs().get(key);
        if (!(value instanceof String text) || text.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Missing or invalid specification: " + key);
        }
        return text;
    }

    private Number numberSpec(ProductResponse product, String key) {
        Object value = product.getSpecs().get(key);
        if (!(value instanceof Number number) || !Double.isFinite(number.doubleValue()) || number.doubleValue() <= 0) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Missing or invalid specification: " + key);
        }
        return number;
    }
}