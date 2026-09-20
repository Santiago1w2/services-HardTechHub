package com.api.v1.compatibilityservice.dto;

import lombok.*;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class ComponentRequest{
    private String type;
    private Long product_id;
}