from transformers import pipeline
import numpy as np
import sys
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics.pairwise import cosine_similarity

# Charger le pipeline de feature-extraction
feature_extractor = pipeline(task="feature-extraction", model="bert-base-uncased")

# Exemple de données de joueur de hockey
joueurs_data = [
    "John Doe, 25 ans, 70 points la saison dernière, 5 ans de contrat restants, 7M",
    "Jane Smith, 28 ans, 60 points la saison dernière, 3 ans de contrat restants, 5M",
    "Alex Johnson, 30 ans, 55 points la saison dernière, 4 ans de contrat restants, 6M",
    "Emily Davis, 22 ans, 80 points la saison dernière, 6 ans de contrat restants, 9M",
    "Chris Brown, 27 ans, 65 points la saison dernière, 2 ans de contrat restants, 4M",
    "Patricia Garcia, 26 ans, 75 points la saison dernière, 5 ans de contrat restants, 8M",
    "Michael Martinez, 24 ans, 85 points la saison dernière, 7 ans de contrat restants, 10M",
    "Linda Rodriguez, 29 ans, 50 points la saison dernière, 3 ans de contrat restants, 3M",
    "David Harris, 31 ans, 45 points la saison dernière, 2 ans de contrat restants, 2M",
    "Sarah Clark, 23 ans, 90 points la saison dernière, 6 ans de contrat restants, 11M",
    "Robert Lewis, 27 ans, 65 points la saison dernière, 4 ans de contrat restants, 5M",
    "Jessica Lee, 28 ans, 60 points la saison dernière, 3 ans de contrat restants, 6M",
    "Daniel Walker, 25 ans, 70 points la saison dernière, 5 ans de contrat restants, 7M",
    "Susan Hall, 26 ans, 75 points la saison dernière, 4 ans de contrat restants, 8M",
    "Matthew Allen, 30 ans, 55 points la saison dernière, 2 ans de contrat restants, 4M",
    "Nancy Young, 22 ans, 80 points la saison dernière, 6 ans de contrat restants, 9M",
    "Anthony Hernandez, 24 ans, 85 points la saison dernière, 7 ans de contrat restants, 10M",
    "Laura King, 31 ans, 45 points la saison dernière, 2 ans de contrat restants, 3M",
    "Brian Wright, 27 ans, 65 points la saison dernière, 5 ans de contrat restants, 7M",
    "Megan Lopez, 29 ans, 50 points la saison dernière, 3 ans de contrat restants, 6M",
    "Kevin Hill, 25 ans, 70 points la saison dernière, 4 ans de contrat restants, 8M",
    "Deborah Scott, 26 ans, 75 points la saison dernière, 5 ans de contrat restants, 9M",
    "Steven Green, 30 ans, 55 points la saison dernière, 2 ans de contrat restants, 5M",
    "Carol Adams, 23 ans, 90 points la saison dernière, 6 ans de contrat restants, 11M",
    "Eric Baker, 28 ans, 60 points la saison dernière, 3 ans de contrat restants, 7M",
    "Amanda Nelson, 24 ans, 85 points la saison dernière, 7 ans de contrat restants, 10M",
    "Ronald Carter, 31 ans, 45 points la saison dernière, 2 ans de contrat restants, 4M",
    "Sandra Mitchell, 26 ans, 75 points la saison dernière, 5 ans de contrat restants, 8M",
    "Joshua Perez, 29 ans, 50 points la saison dernière, 3 ans de contrat restants, 6M",
    "Barbara White, 25 ans, 70 points la saison dernière, 4 ans de contrat restants, 7M",
]

labels = [
    "9e choix overall + Espoir B",
    "Roster player Élite + 50e choix overall",
    "Roster player bon",
    "6e choix overall + Espoir A",
    "13e choix overall + Espoir C",
    "Roster player très bon",
    "3e choix overall + Espoir A",
    "Roster player moyen",
    "Espoir D + 6e tour",
    "1er choix overall + Espoir A+",
    "Roster player bon + 40e choix overall",
    "Roster player moyen + 60e choix overall",
    "9e choix overall + Espoir B",
    "Roster player très bon + 35e choix overall",
    "Espoir C + 7e tour",
    "5e choix overall + Espoir A",
    "2e choix overall + Espoir A",
    "Espoir D + 5e tour",
    "Roster player bon + 45e choix overall",
    "Roster player moyen + 50e choix overall",
    "7e choix overall + Espoir B+",
    "Roster player très bon + 30e choix overall",
    "Espoir C + 8e tour",
    "1er choix overall + Espoir A+",
    "Roster player moyen + 55e choix overall",
    "3e choix overall + Espoir A",
    "Espoir D + 7e tour",
    "Roster player très bon + 35e choix overall",
    "Roster player moyen + 50e choix overall",
    "Roster player bon + 40e choix overall",
]

features = [np.mean(feature_extractor(data)[0], axis=0) for data in joueurs_data]

label_features = [np.mean(feature_extractor(label)[0], axis=0) for label in labels]

# Diviser les données en ensembles d'entraînement et de test
X_train, X_test, y_train, y_test = train_test_split(features, label_features, test_size=0.05, random_state=42)

# Entraîner un modèle de régression
model = RandomForestRegressor()
model.fit(X_train, y_train)

# Faire des prédictions
predictions = model.predict(X_test)

print("-------- PREDICTIONS ------------")
similarities = cosine_similarity(predictions, label_features)

# Trouver le label le plus similaire pour chaque prédiction
predicted_labels = [labels[np.argmax(similarity)] for similarity in similarities]

# Afficher les labels prédits
print("Predicted labels:", predicted_labels)

# Afficher les labels réels pour comparaison
true_labels = [labels[np.argmax(cosine_similarity([true], label_features))] for true in y_test]
print("True labels:", true_labels)