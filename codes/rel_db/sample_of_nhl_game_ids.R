library(odbc)
library(DBI)
library(RSQLite)
library(tidyverse)


mydb <- dbConnect(RSQLite::SQLite(), "C:/Users/huber/OneDrive/Hockey/NHL/DbMatchs.db")

ids <- dbGetQuery(mydb, "SELECT Game_Id FROM PBP
                            ORDER BY RANDOM()
                            LIMIT 300") %>% 
  mutate(season = sample(2010:2022, nrow(.),
                         replace = T),
         id = paste0(season, Game_Id)) %>% 
  pull(., id)

saveRDS(ids, "data/sample_nhl_game_ids.rds")
