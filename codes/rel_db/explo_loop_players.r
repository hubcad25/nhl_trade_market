library(tidyverse)
library(request)
library(ggplot2)

successful_ids <- list()

for (i in 8444000:8500000) {
  print(i)
  player_id <- i
  url <- paste0("https://statsapi.web.nhl.com/api/v1/people/", player_id)

  tryCatch({
    json <- api(url) %>% http()
    print(json)
    successful_ids <- c(successful_ids, player_id)  # Save successful ID to the list
  }, error = function(e) {
    # Error handling code
    cat("Error occurred for player ID:", player_id, "\n")
    cat("Error message:", conditionMessage(e), "\n")
  })
}

# Save the list of successful IDs to a file
saveRDS(successful_ids, "successful_ids.rds")

test <- readRDS("data/explo_ids/successful_ids.rds")
