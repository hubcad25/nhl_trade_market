# Graphiques d'interprétation du modèle de valeur d'échange (fit_trade_value.py, issue 1op)
#
# Deux graphiques :
#   kii_coefficients.png — coefficients postérieurs (moyenne ± écart-type) par
#     famille, features partagées skater/goalie annotées "pooled" (hyper-prior
#     commun, le mécanisme de pooling partiel qui motive la réécriture bayésienne)
#   kii_top_players.png  — top 20 joueurs par valeur normalisée, avec intervalle
#     de crédibilité à 90% par tirage postérieur (pas une heuristique après-coup)
#     — Erik Karlsson (trade 4625) doit montrer un intervalle nettement plus
#     large que les autres, cas qui motive l'incertitude par prédiction
#
# Lit  data/enriched/value_model.json
#      data/enriched/values.jsonl
#      data/resolved/classified_elements.jsonl (noms de joueurs)
#      data/enriched/value_features.jsonl (org_climate -> impasse contractuelle)
#
# Usage: Rscript graphs/plot_value_model.R

library(ggplot2)
library(dplyr)
library(jsonlite)
library(tidyr)

MODEL_PATH <- "data/enriched/value_model.json"
VALUES_PATH <- "data/enriched/values.jsonl"
ELEMENTS_PATH <- "data/resolved/classified_elements.jsonl"
QUALI_PATH <- "data/enriched/value_features.jsonl"

model <- fromJSON(MODEL_PATH, simplifyVector = FALSE)
shared_features <- names(model$shared_features)

# --- Graphique 1 : coefficients ---------------------------------------------

coef_rows <- lapply(names(model$families), function(fam) {
  f <- model$families[[fam]]
  data.frame(
    family = fam,
    feature = unlist(f$features),
    mean = unlist(f$weight_mean),
    sd = unlist(f$weight_sd),
    stringsAsFactors = FALSE
  )
})
coef_df <- bind_rows(coef_rows) %>%
  mutate(
    family = factor(family, levels = c("skater", "goalie", "pick"),
                     labels = c("Skaters", "Goalies", "Draft picks")),
    pooled = feature %in% shared_features,
    sign = ifelse(mean >= 0, "Positive", "Negative")
  ) %>%
  group_by(family) %>%
  mutate(feature = factor(feature, levels = feature[order(mean)])) %>%
  ungroup()

p_coef <- ggplot(coef_df, aes(x = mean, y = feature, color = sign)) +
  geom_vline(xintercept = 0, color = "grey70", linewidth = 0.4) +
  geom_errorbarh(aes(xmin = mean - sd, xmax = mean + sd), height = 0, linewidth = 0.6) +
  geom_point(aes(shape = pooled), size = 2.4) +
  scale_color_manual(values = c("Negative" = "#b07a5c", "Positive" = "#4c7a8c"), name = NULL) +
  scale_shape_manual(values = c(`TRUE` = 17, `FALSE` = 16),
                      labels = c(`TRUE` = "Pooled (skater+goalie)", `FALSE` = "Family-specific"),
                      name = NULL) +
  facet_grid(rows = vars(family), scales = "free_y", space = "free_y") +
  labs(x = "Standardized weight, posterior mean ± sd (contribution to log value)", y = NULL) +
  theme_minimal(base_size = 12) +
  theme(
    panel.grid.minor = element_blank(),
    panel.grid.major.y = element_blank(),
    strip.text = element_text(face = "bold", hjust = 0),
    legend.position = "top"
  )

ggsave("graphs/kii_coefficients.png", p_coef, width = 9, height = 8, dpi = 150, bg = "white")

# --- Graphique 2 : top joueurs avec incertitude -----------------------------

values <- stream_in(file(VALUES_PATH), verbose = FALSE)
elements <- stream_in(file(ELEMENTS_PATH), verbose = FALSE)
quali <- stream_in(file(QUALI_PATH), verbose = FALSE)

elements_flat <- elements %>%
  transmute(
    trade_id, receives_key, element_index,
    trade_year = substr(trade_date, 1, 4),
    player_name = element$tsn_name
  )

quali_flat <- quali %>%
  transmute(
    trade_id, receives_key, element_index,
    contract_impasse = ifelse(is.na(fields$org_climate), FALSE, fields$org_climate == "contract_impasse")
  )

top_players <- values %>%
  filter(family %in% c("skater", "goalie")) %>%
  left_join(elements_flat, by = c("trade_id", "receives_key", "element_index")) %>%
  left_join(quali_flat, by = c("trade_id", "receives_key", "element_index")) %>%
  mutate(contract_impasse = tidyr::replace_na(contract_impasse, FALSE)) %>%
  filter(!is.na(player_name)) %>%
  arrange(desc(normalized_value_mean)) %>%
  slice_head(n = 20) %>%
  mutate(
    row_key = paste0("r", row_number()),
    label = paste0(player_name, ", ", trade_year),
    dispute_label = ifelse(contract_impasse, "Contract dispute", "No dispute")
  )
# factor(levels=, labels=) merges rows whose LABEL collides (e.g. Mikko Rantanen
# traded twice in 2025) into a single level — row_key (always unique) drives
# vertical position, scale_y_discrete's labels= just draws the display text
# per key, so duplicate names stay on their own row.
top_players$row_key <- factor(top_players$row_key, levels = rev(top_players$row_key))
row_key_labels <- setNames(top_players$label, top_players$row_key)

p_top <- ggplot(top_players, aes(y = row_key, x = normalized_value_mean, color = dispute_label)) +
  geom_errorbarh(aes(xmin = normalized_value_ci_low, xmax = normalized_value_ci_high), height = 0.25, linewidth = 0.9) +
  geom_point(size = 2.6) +
  geom_text(aes(x = normalized_value_ci_high, label = sprintf("%.2f", normalized_value_mean)),
            hjust = -0.25, size = 3.4, color = "grey20") +
  scale_color_manual(values = c("Contract dispute" = "#c9974e", "No dispute" = "#6f8f7e"), name = NULL) +
  scale_y_discrete(labels = row_key_labels) +
  scale_x_continuous(expand = expansion(mult = c(0.02, 0.18))) +
  labs(x = "Relative value, posterior mean with 90% credible interval\n(1.0 = a late 1st-round pick, ~1 year before the draft)",
       y = NULL) +
  theme_minimal(base_size = 12) +
  theme(
    panel.grid.minor = element_blank(),
    panel.grid.major.y = element_blank(),
    legend.position = "bottom",
    plot.margin = margin(10, 20, 10, 10)
  )

ggsave("graphs/kii_top_players.png", p_top, width = 11, height = 9, dpi = 150, bg = "white")

cat("écrit -> graphs/kii_coefficients.png, graphs/kii_top_players.png\n")
