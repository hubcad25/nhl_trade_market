trades <- read.csv2("data/explo_transformer/trades.csv")

trades <- c(trades$package_a, trades$package_b)

trade_elements <- unlist(strsplit(trades, " \\+ "))

trade_elements <- lapply(trade_elements, function(x) {
  x <- gsub(", Special situation: none", "", x)
  x <- gsub(", Special situation: None", "", x)
  x <- gsub(", Special situation: in Russia", "", x)
  x <- gsub(", Special situation: does not want to sign", "", x)
  x <- gsub("\\(", "", x)
  x <- gsub("\\)", "", x)
  x
})

# Convertir la liste en vecteur
trade_elements <- unlist(trade_elements)

trade_elements

write.csv(trade_elements, "data/explo_transformer/trade_elements.csv", row.names = FALSE)
