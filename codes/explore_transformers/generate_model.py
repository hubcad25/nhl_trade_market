import numpy as np
import pickle
from transformers import pipeline, TFAutoModel, AutoTokenizer
import pandas as pd
import sys

### Charger les trades avec pandas
trades = pd.read_csv('data/explo_transformer/trades.csv',
                     sep=';')

# Charger le pipeline de feature-extraction
feature_extractor = pipeline(task="feature-extraction", model="bert-base-uncased")

# Sauvegarder le modèle et le tokenizer
feature_extractor.model.save_pretrained('codes/explore_transformers/model/saved_model')
feature_extractor.tokenizer.save_pretrained('codes/explore_transformers/model/saved_model')

print("--------- extractor done ----------")

input = np.concatenate((trades['package_a'].values, trades['package_b'].values))

output = np.concatenate((trades['package_b'].values, trades['package_a'].values))

# Extraire les caractéristiques et aplatir les vecteurs des joueurs
input_features = [np.mean(feature_extractor(data)[0], axis=0) for data in input]

# Extraire les caractéristiques et aplatir les vecteurs des labels
output_features = [np.mean(feature_extractor(label)[0], axis=0) for label in output]

# Enregistrer les caractéristiques et les labels
with open('codes/explore_transformers/model/input_features.pkl', 'wb') as f:
    pickle.dump(input_features, f)

with open('codes/explore_transformers/model/output_features.pkl', 'wb') as f:
    pickle.dump(output_features, f)

# Entraîner un modèle de régression et l'enregistrer
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split

# Diviser les données en ensembles d'entraînement et de test
X_train, X_test, y_train, y_test = train_test_split(input_features, output_features, test_size=0.05, random_state=40)

# Entraîner un modèle de régression
model = RandomForestRegressor()
model.fit(X_train, y_train)

# Enregistrer le modèle
with open('codes/explore_transformers/model/trained_model.pkl', 'wb') as f:
    pickle.dump(model, f)

print("Features, label features, and model saved successfully.")
