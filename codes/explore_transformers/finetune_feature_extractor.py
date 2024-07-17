import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset
from transformers import BertTokenizer, BertModel, AdamW
import matplotlib.pyplot as plt
import sys

# Charger les éléments de trades
trades = pd.read_csv('data/explo_transformer/trade_elements.csv', sep=';')

# Dataset personnalisé
class TradesDataset(Dataset):
    def __init__(self, trades, tokenizer, max_len):
        self.trades = trades
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.trades)

    def __getitem__(self, index):
        trade = str(self.trades.iloc[index, 0])
        encoding = self.tokenizer.encode_plus(
            trade,
            add_special_tokens=True,
            max_length=self.max_len,
            return_token_type_ids=False,
            padding='max_length',
            return_attention_mask=True,
            return_tensors='pt',
        )
        return {
            'trade_text': trade,
            'input_ids': encoding['input_ids'].flatten(),
            'attention_mask': encoding['attention_mask'].flatten()
        }
    
# Charger le tokenizer et le modèle BERT
tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
bert_model = BertModel.from_pretrained('bert-base-uncased')

# Définir le modèle de finetuning
class FineTuneBERT(nn.Module):
    def __init__(self, bert_model):
        super(FineTuneBERT, self).__init__()
        self.bert = bert_model
        self.dropout = nn.Dropout(0.3)
        self.linear = nn.Linear(self.bert.config.hidden_size, 1)  # Ajustez la sortie selon vos besoins

    def forward(self, input_ids, attention_mask):
        outputs = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        pooled_output = outputs[1]
        dropped_output = self.dropout(pooled_output)
        return self.linear(dropped_output)
    
# Configurer les paramètres pour l'entraînement
# Paramètres
MAX_LEN = 128
BATCH_SIZE = 16
EPOCHS = 100
LEARNING_RATE = 2e-5

# Créer le DataLoader
from torch.utils.data import DataLoader

dataset = TradesDataset(trades, tokenizer, MAX_LEN)
dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True)

# Initialiser le modèle et l'optimiseur
model = FineTuneBERT(bert_model)
optimizer = AdamW(model.parameters(), lr=LEARNING_RATE)
criterion = nn.CosineEmbeddingLoss()  # Adaptez la loss function selon vos besoins

# Implémenter la boucle d'entraînement et afficher la courbe d'apprentissage

# Dummy targets for CosineEmbeddingLoss, can be adjusted based on actual use case
targets = torch.ones(BATCH_SIZE)

# Training loop
losses = []

for epoch in range(EPOCHS):
    model.train()
    total_loss = 0
    for batch in dataloader:
        input_ids = batch['input_ids']
        attention_mask = batch['attention_mask']
        
        optimizer.zero_grad()
        outputs = model(input_ids, attention_mask)
        # Assuming the dummy target of all ones, this should be adapted based on actual requirements
        loss = criterion(outputs, outputs, targets[:outputs.size(0)])
        loss.backward()
        optimizer.step()
        
        total_loss += loss.item()
    
    avg_loss = total_loss / len(dataloader)
    losses.append(avg_loss)
    print(f'Epoch {epoch + 1}/{EPOCHS}, Loss: {avg_loss:.4f}')

# Affichage de la courbe d'apprentissage
plt.plot(range(EPOCHS), losses, marker='o')
plt.xlabel('Epochs')
plt.ylabel('Loss')
plt.title('Learning curve')
plt.show()

