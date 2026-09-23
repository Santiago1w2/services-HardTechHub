package com.api.v1.compatibilityservice.repository;

import com.api.v1.compatibilityservice.dto.CompatibilityRequest;
import com.api.v1.compatibilityservice.dto.CompatibilityResponse;
import java.sql.PreparedStatement;
import java.util.Locale;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

@Repository
public class CompatibilityRepository {
    private final JdbcTemplate jdbc;

    public CompatibilityRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional
    public void save(CompatibilityRequest request, CompatibilityResponse response) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                "INSERT INTO compatibility_checks(compatible) VALUES (?)", new String[]{"id"});
            statement.setBoolean(1, response.isCompatible());
            return statement;
        }, keys);
        long checkId = keys.getKey().longValue();
        for (var component : request.getComponents()) {
            jdbc.update("INSERT INTO compatibility_components(check_id, component_type, product_id) VALUES (?,?,?)",
                checkId, component.getType().toLowerCase(Locale.ROOT), component.getProduct_id());
        }
        for (int i = 0; i < response.getMessages().size(); i++) {
            jdbc.update("INSERT INTO compatibility_messages(check_id, position, message) VALUES (?,?,?)",
                checkId, i, response.getMessages().get(i));
        }
    }
}
