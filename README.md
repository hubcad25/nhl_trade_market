# nhl_trade_market

# Purpose of this project
This project seeks to give a prediction of the trade package that a NHL player could fetch on the NHL trade market. The predictions would be based on what similar sets of assets have been traded for in the past.

For example, if I wanted to know what could currently fetch the Rangers if they were to trade Alexis Lafrenière, the model would do the following steps:
    1. Search for players with a similar profile to Alexis Lafrenière who were traded
    2. Find patterns in the types of trades in which these players have been involved
        - Was the player the main piece of the trade?
        - How many assets are included?
        - What types of assets? Established offensive top6 player? 19 YO prospect drafted in the late 1st round? etc.
    3. Predict a list of different patterns of possible packages (and their probability)
    4. Suggest packages following those patterns in the current NHL

# Outline of the projects
The global projects for this purpose to be materialized are the following:
- [] Build a relational database containing players and teams data
- [] Build a dataset containing NHL trades
- [] Train a transformer machine learning model

# Organization of the repo
The repo is currently organized through the following folders:
- **data**: contains all datasets used throughout the repo. Internally organized by project.
- **diagrams**: contains relevant diagrams made used to visualize the different operations of the repo. Internally organized by project. 
- **code**: contains all relevant code files. Internally organized by project.
- **documentation**: contains relevant documentation

# Package
There might be a reflexion to be had for developing a package used to access data in the different databases.