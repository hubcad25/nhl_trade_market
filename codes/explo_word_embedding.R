load(url("https://cbail.github.io/Elected_Official_Tweets.Rdata"))

library(tidytext)
library(dplyr)
# We want to use original tweets, not retweets:
elected_no_retweets <- elected_official_tweets %>%
  filter(is_retweet == F) %>%
  select(c("text"))
#create tweet id
elected_no_retweets$postID<-row.names(elected_no_retweets)

library(widyr)
#create context window with length 8
tidy_skipgrams <- elected_no_retweets %>%
  unnest_tokens(ngram, text, token = "ngrams", n = 8) %>%
  mutate(ngramID = row_number()) %>% 
  tidyr::unite(skipgramID, postID, ngramID) %>%
  unnest_tokens(word, ngram)
