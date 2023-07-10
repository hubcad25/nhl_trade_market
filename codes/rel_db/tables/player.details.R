# Packages ----------------------------------------------------------------
library(tidyverse)
library(httr)
library(request)

# Data --------------------------------------------------------------------
nhl_ids <- unlist(readRDS("data/explo_ids/successful_ids.rds"))

nhl_ids <- nhl_ids[sample(1:length(nhl_ids), 300, replace = T)]

# Functions ---------------------------------------------------------------

# This function calls the nhl api to return a list of infos of a single player
fetch_single_player_infos <- function(player_id){
  player_id <- as.character(player_id)
  url <- paste0("https://statsapi.web.nhl.com/api/v1/people/", player_id)
  list <- (api(url) %>% http())$people[[1]]
  return(list)
}

#list <- fetch_single_player_infos(nhl_ids[89])

# This function applies fetch_single_player_infos to a vector of player_ids
fetch_players_infos <- function(player_ids){
  return(pbapply::pblapply(player_ids, fetch_single_player_infos))
}

## These next functions get information in a single player list
get_player_fullname <- function(list){
  return(list$fullName)
}

get_player_firstName <- function(list){
  return(list$firstName)
}

get_player_lastName <- function(list){
  return(list$lastName)
}

get_player_positions <- function(list){
  return(list$primaryPosition$abbreviation)
}

get_player_positions_type <- function(list){
  return(list$primaryPosition$type)
}

get_player_nationality <- function(list){
  return(list$nationality)
}

get_player_birthCountry <- function(list){
  return(list$birthCountry)
}

get_player_dob <- function(list){
  return(list$birthDate)
}

get_player_city <- function(list){
  return(list$birthCity)
}

get_player_stateProvince <- function(list){
  return(list$birthStateProvince)
}

get_player_feet <- function(list){
  str <- list$height
  if (is.null(str)){
    return(NA)
  } else {
    split <- strsplit(str, "'")
    return(as.numeric(split[[1]][1]))
  }
}

get_player_inches <- function(list){
  str <- list$height
  if (is.null(str)){
    return(NA)
  } else {
    split <- strsplit(str, "'")
  }
  i <- gsub('\"',"", split[[1]][2])
  i <- gsub(" ","", i)
  return(as.numeric(i))
}

get_player_weight <- function(list){
  return(list$weight)
}

get_player_number <- function(list){
  n <- list$primaryNumber
  if (purrr::is_empty(n)){
    return(NA)
  }
  n <- as.numeric(n)
  return(n)
}

get_player_laterality <- function(list){
  return(list$shootsCatches)
}

get_player_active <- function(list){
  return(list$active)
}

## WARNING:
### These functions need to return a vector when integrated into a
### sapply(list, function).
### They also need to start by "get_player"



# Create dataframe skeleton -----------------------------------------------

df <- data.frame(nhl_id = nhl_ids)


# Applying functions to a list of nhl ids ---------------------------------

list <- fetch_players_infos(nhl_ids)

env <- lsf.str()
# only keep functions starting with "get_player" in it 
fcts <- env[grepl("get_player", env)]

for (f in fcts) {
  col <- gsub("get_player_", "", f)
  df[[col]] <- sapply(list, function(x) do.call(f, list(x)))
  print(col)
}
