# nhl_trade_market

# Purpose of this project
This project seeks to give a prediction of the trade package that a NHL player could fetch on the NHL trade market. The predictions would be based on what similar sets of assets have been traded for in the past.

For example, if I wanted to know what could currently fetch the Rangers if they were to trade Alexis Lafrenière, the model would do the following steps:
- Search for players with a similar profile to Alexis Lafrenière who were traded
- Find patterns in the types of trades in which these players have been involved
    - Was the player the main piece of the trade?
    - How many assets are included?
    - What types of assets? Established offensive top6 player? 19 YO prospect drafted in the late 1st round? etc.
- Predict a list of different patterns of possible packages (and their probability)
- Suggest packages following those patterns in the current NHL

# Outline of the projects
The global projects for this purpose to be materialized are the following:
- [] Build a relational database containing players data (postgres sql)
    - [] Automatic collection
    - [] Manually input data through a UI when necessary
- [] Build a function that retrieves data on a player at a certain point in time (in the relational db and apis), returns a json
- [] Build a function that transforms this data into a text report
- [] Finetune a feature extractor that transforms this text report into a set of numerical features
- [] Build a dataset containing NHL trades
- [] Train a neural network

# Organization of the repo
The repo is currently organized through the following folders:
- **data**: contains all datasets used throughout the repo. Internally organized by project.
- **diagrams**: contains relevant diagrams made used to visualize the different operations of the repo. Internally organized by project. 
- **code**: contains all relevant code files. Internally organized by project.
- **documentation**: contains relevant documentation

# Package
There might be a reflexion to be had for developing a package used to access data in the different databases.

# Data Engineering
- Dataflow
- Flow-based programming
- Dagster vs AirFlow

## Things to keeps in mind of
- We will use [PST](https://www.prosportstransactions.com/hockey/) to get NHL historical trades and add column where to alert where it requires human intervention (trade conditions, etc.)
- Create table with our own ID system that will link it to all other Ids (NHL, HRef, EliteProspects, etc.)
- We want a function that returns a text report :
    """
    get_player_report(id_player, date)
    
    Depth, Position: F, 27 year old, Quality: 3, Contract: 1 year 900000
    """
