{% macro wilson_interval(numerator_count, denominator_count, bound) -%}
case
    when {{ denominator_count }} = 0 then null
    else greatest(
        0.0,
        least(
            1.0,
            (
                ({{ numerator_count }}::double / {{ denominator_count }})
                + 3.8414588206941254 / (2.0 * {{ denominator_count }})
                {% if bound == 'lower' %}-{% elif bound == 'upper' %}+{% else %}
                    {{ exceptions.raise_compiler_error("wilson_interval bound must be lower or upper") }}
                {% endif %}
                1.959963984540054 * sqrt(
                    (
                        ({{ numerator_count }}::double / {{ denominator_count }})
                        * (1.0 - ({{ numerator_count }}::double / {{ denominator_count }}))
                        + 3.8414588206941254 / (4.0 * {{ denominator_count }})
                    ) / {{ denominator_count }}
                )
            ) / (1.0 + 3.8414588206941254 / {{ denominator_count }})
        )
    )
end
{%- endmacro %}
