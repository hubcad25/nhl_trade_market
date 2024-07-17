# Charger les bibliothèques nécessaires
library(forecast)
library(tseries)

# Générer des données exemple
set.seed(123)
data_player_a <- ts(rnorm(n = 40, mean = 10, sd = 1))
plot(data_player_a, main = "Data Player A")

data_player_b <- ts(rnorm(n = 12, mean = 7, sd = 6))
plot(data_player_b, main = "Data Player B")

# Fonction pour appliquer un lissage exponentiel
ewma <- function(data, alpha = 0.3) {
  filtered <- rep(NA, length(data))
  filtered[1] <- data[1]
  for (i in 2:length(data)) {
    filtered[i] <- alpha * data[i] + (1 - alpha) * filtered[i - 1]
  }
  return(filtered)
}

# Appliquer le lissage exponentiel aux données
data_player_a_ewma <- ewma(data_player_a, alpha = 0.3)
data_player_b_ewma <- ewma(data_player_b, alpha = 0.3)

# Ajuster un modèle ARIMA sur les données lissées
model_a <- auto.arima(data_player_a_ewma)
forecast_a <- forecast(model_a, h = 1)
print(forecast_a)

model_b <- auto.arima(data_player_b_ewma)
forecast_b <- forecast(model_b, h = 1)
print(forecast_b)


#### PREDICT POINTS

# Générer des données exemple
set.seed(123)
data_points <- sample(0:4, size = 50, replace = TRUE, prob = c(0.4, 0.3, 0.2, 0.09, 0.01))  # Points entre 0 et 4
data_points <- ts(data_points)
plot(data_points, main = "Data Player A")

data_points_emwa <- ewma(data_points, alpha = 0.3)

model <- auto.arima(data_points_emwa)
forecast <- forecast(model, h = 1)
print(forecast)

