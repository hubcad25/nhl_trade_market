# Packages ----------------------------------------------------------------
library(tidyverse)
library(httr)
library(request)

# Functions ---------------------------------------------------------------

# This function calls the nhl api
fetch_list_of_infos <- function(player_id){
  player_id <- as.character(player_id)
  url <- paste0("https://statsapi.web.nhl.com/api/v1/people/", player_id)
  list <- api(url) %>% http()
  return(list)
}

#list <- fetch_list_of_infos(nhl_ids[1])

## These next functions get information in the list returned by get_list_of_infos
get_player_fullname <- function(list){
  return(list$people[[1]]$fullName)
}

get_player_firstName <- function(list){
  return(list$people[[1]]$firstName)
}

get_player_lastName <- function(list){
  return(list$people[[1]]$lastName)
}

get_player_positions <- function(list){
  return(list$people[[1]]$primaryPosition$abbreviation)
}

get_player_positions_type <- function(list){
  return(list$people[[1]]$primaryPosition$type)
}

get_player_nationality <- function(list){
  return(list$people[[1]]$nationality)
}

get_player_birthCountry <- function(list){
  return(list$people[[1]]$birthCountry)
}

get_player_dob <- function(list){
  return(list$people[[1]]$birthDate)
}

get_player_city <- function(list){
  return(list$people[[1]]$birthCity)
}

get_player_stateProvince <- function(list){
  return(list$people[[1]]$birthStateProvince)
}

get_player_feet <- function(list){
  str <- list$people[[1]]$height
  split <- strsplit(str, "'")
  return(as.numeric(split[[1]][1]))
}

get_player_inches <- function(list){
  str <- list$people[[1]]$height
  split <- strsplit(str, "'")
  i <- gsub('\"',"", split[[1]][2])
  i <- gsub(" ","", i)
  return(as.numeric(i))
}

get_player_weight <- function(list){
  return(list$people[[1]]$weight)
}

get_player_number <- function(list){
  return(as.numeric(list$people[[1]]$primaryNumber))
}

get_player_laterality <- function(list){
  return(list$people[[1]]$shootsCatches)
}

get_player_active <- function(list){
  return(list$people[[1]]$active)
}


# Applying functions to a list of nhl ids ---------------------------------
nhl_ids <- readRDS("data/sample_nhl_ids.rds")

list <- pbapply::pblapply(nhl_ids, fetch_list_of_infos)

env <- lsf.str()
fcts <- env[grepl("get", env)]

