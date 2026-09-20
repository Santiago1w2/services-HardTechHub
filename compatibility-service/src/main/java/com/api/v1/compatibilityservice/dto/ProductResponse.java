package com.api.v1.compatibilityservice.dto;


import lombok.*;

import java.util.Map;
@Getter
@NoArgsConstructor
@AllArgsConstructor
public class ProductResponse{
        private Long id;
        private String sku;
        private String name;
        private String description;
        private String price;
        private Map<String, Object> specs;
        private String image_url;
        private Boolean is_active;
        private String created_at;
        private String category;
        private String brand;

}