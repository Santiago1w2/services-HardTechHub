package com.api.v1.compatibilityservice.service;

import com.api.v1.compatibilityservice.client.CatalogClient;
import com.api.v1.compatibilityservice.dto.CompatibilityRequest;
import com.api.v1.compatibilityservice.dto.CompatibilityResponse;
import com.api.v1.compatibilityservice.dto.ComponentRequest;
import com.api.v1.compatibilityservice.dto.ProductResponse;
import lombok.Getter;
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

        for (ComponentRequest component : request.getComponents()) {
            ProductResponse product =catalogClient.getProduct(component.getProduct_id());
            switch (component.getType().toLowerCase()) {
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
                    String.valueOf(cpu.getSpecs().get("socket"));

            String motherboardSocket =
                    String.valueOf(motherboard.getSpecs().get("socket"));

            if (cpuSocket.equals(motherboardSocket)) {
                messages.add("CPU y Motherboard usan "+ cpuSocket + ".");

            } else {
                messages.add("La CPU usa "+ cpuSocket+ ", pero la Motherboard usa "+ motherboardSocket + ".");

                compatible = false;
            }
        }


        if (ram != null && motherboard != null) {
            String ramType =String.valueOf(ram.getSpecs().get("memory_type"));

            String motherboardRamType =String.valueOf(motherboard.getSpecs().get("memory_type"));

            if (ramType.equals(motherboardRamType)) {
                messages.add("RAM y Motherboard usan "+ ramType+ ".");
            } else {
                messages.add( "La RAM es "+ ramType + ", pero la Motherboard requiere " + motherboardRamType + ".");
                compatible = false;
            }
        }

        if (gpu != null && psu != null) {
            Number recommendedPsu = (Number) gpu.getSpecs().get("recommended_psu_watts");
            Number psuWattage = (Number) psu.getSpecs().get("wattage");
            if (recommendedPsu != null && psuWattage != null) {
                if (psuWattage.doubleValue() >= recommendedPsu.doubleValue()) {
                    messages.add("La fuente de " + psuWattage + " W cumple el requisito de "+ recommendedPsu+ " W de la GPU.");
                } else {
                    messages.add("La fuente de "+ psuWattage+ " W no alcanza los "+ recommendedPsu+ " W recomendados por la GPU.");
                    compatible = false;
                }
            }
        }

        return new CompatibilityResponse(compatible, messages);
    }
}