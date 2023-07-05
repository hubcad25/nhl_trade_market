# https://www.learnbymarketing.com/tutorials/neural-networks-in-r-tutorial/

library(neuralnet)


bnk <- read.csv("data/explo_neuralnet/bank-full.csv", sep = ";")

bnk$balance <- clessnverse::normalize_min_max(bnk$balance)
bnk$age <- clessnverse::normalize_min_max(bnk$age)
bnk$previous <- clessnverse::normalize_min_max(bnk$previous)
bnk$campaign <- clessnverse::normalize_min_max(bnk$campaign)

bnk$education <- relevel(factor(bnk$education), ref = "secondary")
head(model.matrix(~education, data=bnk))

bnk_matrix <- model.matrix(~age+job+marital+education
                           +default+balance+housing
                           +loan+poutcome+campaign
                           +previous+y, data=bnk)

colnames(bnk_matrix) <- gsub("-", "", colnames(bnk_matrix))


col_list <- paste(c(colnames(bnk_matrix[,-c(1,28)])),collapse="+")
col_list <- paste(c("yyes~",col_list),collapse="")
f <- formula(col_list)

set.seed(46553)

sample <- sample(c(TRUE, FALSE), nrow(df), replace=TRUE, prob=c(0.7,0.3,))
train  <- df[sample, ]
test   <- df[!sample, ]

nmodel <- neuralnet(f,data=bnk_matrix[sample(1:nrow(bnk_matrix), 1000),],hidden=c(2,2),
                    threshold = 0.01,
                    learningrate.limit = NULL,
                    learningrate.factor =
                      list(minus = 0.5, plus = 1.2),
                    algorithm = "rprop+")

plot(nmodel)

