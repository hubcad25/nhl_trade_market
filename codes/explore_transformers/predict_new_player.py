import pickle
import numpy as np
from transformers import TFAutoModel, AutoTokenizer, pipeline
from sklearn.metrics.pairwise import cosine_similarity
import pandas as pd

### Charger les trades avec pandas
trades = pd.read_csv('data/explo_transformer/trades.csv',
                     sep=';')

# Charger les caractéristiques et les labels
with open('codes/explore_transformers/model/input_features.pkl', 'rb') as f:
    features = pickle.load(f)

with open('codes/explore_transformers/model/output_features.pkl', 'rb') as f:
    label_features = pickle.load(f)

output = np.concatenate((trades['package_b'].values, trades['package_a'].values))

# Charger le modèle et le tokenizer sauvegardés
model = TFAutoModel.from_pretrained('codes/explore_transformers/model/saved_model')
tokenizer = AutoTokenizer.from_pretrained('codes/explore_transformers/model/saved_model')

# Recréer le pipeline de feature-extraction
feature_extractor = pipeline(task="feature-extraction", model=model, tokenizer=tokenizer)

# Exemple de nouveau joueur
new_joueur_data = "(Prime, Position: F, 28 year old, Quality: 9, Contract: 1 year 7000000, Special situation: none)"

# Extraire les caractéristiques du nouveau joueur
new_joueur_features = np.mean(feature_extractor(new_joueur_data)[0], axis=0).reshape(1, -1)

# Charger le modèle de régression
with open('codes/explore_transformers/model/trained_model.pkl', 'rb') as f:
    model = pickle.load(f)

# Faire des prédictions
new_predictions = model.predict(new_joueur_features)

# Calculer la similarité cosinus entre les prédictions et les caractéristiques des labels d'origine
similarities = cosine_similarity(new_predictions, label_features)

# Trouver le label le plus similaire pour chaque prédiction
predicted_labels = [output[np.argmax(similarity)] for similarity in similarities]

# Afficher les labels prédits
print("Predicted labels:", predicted_labels)
