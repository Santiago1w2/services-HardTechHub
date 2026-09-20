package com.api.v1.compatibilityservice.dto;

import lombok.*;

import java.util.List;
@Setter
@Getter
@NoArgsConstructor
@AllArgsConstructor
public class CompatibilityResponse{
    private boolean compatible;
    private List<String> messages;
}