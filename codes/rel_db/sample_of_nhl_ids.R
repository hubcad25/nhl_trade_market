library(odbc)
library(DBI)
library(RSQLite)
library(tidyverse)


mydb <- dbConnect(RSQLite::SQLite(), "C:/Users/huber/OneDrive/Hockey/NHL/DbMatchs.db")

ids <- unlist(dbGetQuery(mydb, "SELECT p1_ID FROM PBP
                            ORDER BY RANDOM()
                            LIMIT 300") %>% 
           drop_na())

saveRDS(ids, "data/sample_nhl_ids.rds")
