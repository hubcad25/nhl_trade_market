import streamlit as st
import numpy as np
hockey_emoji_png = "https://em-content.zobj.net/source/apple/81/ice-hockey-stick-and-puck_1f3d2.png"
page_name = 'Trades'
st.set_page_config(page_title=f'The Draft Digest - {page_name}', page_icon=hockey_emoji_png,
                   layout="centered", initial_sidebar_state='auto',
                   menu_items={'Get help': 'https://www.streamlit.io/'},)

# Title
st.title('NHL Trade market')
st.subheader(page_name)
st.write("Model outputs. It is still in development. Don't take it too seriously. It's just for fun.")
# Add separator
st.markdown('---')

# Names of NHL teams
teams = ['Anaheim Ducks', 'Arizona Coyotes', 'Boston Bruins', 'Buffalo Sabres', 'Calgary Flames', 'Carolina Hurricanes', 'Chicago Blackhawks', 'Colorado Avalanche', 'Columbus Blue Jackets', 'Dallas Stars', 'Detroit Red Wings', 'Edmonton Oilers', 'Florida Panthers', 'Los Angeles Kings', 'Minnesota Wild', 'Montreal Canadiens', 'Nashville Predators', 'New Jersey Devils', 'New York Islanders', 'New York Rangers', 'Ottawa Senators', 'Philadelphia Flyers', 'Pittsburgh Penguins', 'San Jose Sharks', 'St. Louis Blues', 'Tampa Bay Lightning', 'Toronto Maple Leafs', 'Vancouver Canucks', 'Vegas Golden Knights', 'Washington Capitals', 'Winnipeg Jets']

add_trade, edit_trade, delete_trade = st.tabs(["Add Trade", "Edit Trade", "Delete Trade"])

asset_maping_json = {
    'Player': {
        'Id' : np.random.randint(1, 100000),
        'First Name': 'John',
        'Last Name': 'Doe',
        'Position': 'F',
        'Position_Desc': 'Forward',
        'DOB': '1990-01-01',
        'Nationality' : 'CAN',
        'Height': '6-0',
        'Weight': '200',
        'Shoots': 'L',
        'Draft Year': '2010',
        'Draft Round': '1',
        'Draft Overall': '1',
        'Draft Team': 'Edmonton Oilers',
        'Player_type' : 'NHLer',
        'url': 'https://www.eliteprospects.com/player/12345/john-doe',
        'Stats' : [{
            "Season Start Year": 2020,
            "Season End Year": 2021,
            "Team": "Edmonton Oilers",
            "League": "NHL",
            "GP": 56,
            "G": 25,
            "A": 30,
            "PTS": 55,
            "PIM": 20,
            "PM": 10,
            "PPG": 0.98,
            "SHG": 0.02,
            "GWG": 5,
            "OTG": 2,
            "S": 150,
            "S%": 0.15,
            "TOI": 1200,
            "ATOI": 20,
            "HIT": 50,
            "BLK": 30,
            "FOW": 100,
            "FOL": 50,
            "FO%": 0.67,
            "CF": 500,
            "CA": 400,
            "CF%": 0.55,
            "FF": 450,
            "FA": 350,
            "FF%": 0.56,
            "SF": 300,
            "SA": 200,

    }],
    "Salary history": [{
        "Season Start Year": 2020,
        "Season End Year": 2021,
        "Team": "Edmonton Oilers",
        "League": "NHL",
        "Salary": 1000000,
        "Cap Hit": 1000000,
        "Signing Bonus": 100000,
        "Performance Bonus": 50000,
        "Average Annual Value": 1000000,
        "Total Salary": 1000000,
        "Total Cap Hit": 1000000,
        "Total Signing Bonus": 100000,
        "Total Performance Bonus": 50000,
        "Total Average Annual Value": 1000000,
        "Total Total Salary": 1000000,
        "Total Total Cap Hit": 1000000,
        "Total Total Signing Bonus": 100000,
        "Total Total Performance Bonus": 50000,
        "Total Total Average Annual Value": 1000000,
    }]}
    ,
    'Draft Pick': {
        'Id' : np.random.randint(1, 100000),
        'Year': 2021,
        'Round': 1,
        'Overall': 20,
        'Team': 'TBD',
        'Player': 'TBD',
        'transactions' : [{
            'Date': '2021-07-01',
            'Team_from': 'Edmonton Oilers',
            'Team_to': 'Montreal Canadiens',
            'Details': {
                'transaction_type': 'Trade',
                'Details': {
                    'Traded with' : None,
                    'Traded for': [{'Player': 'John Doe',
                                    'PlayerId': 12345,
                                    'Position': 'F',
                                    'Team': 'Edmonton Oilers',
                                    'url': 'https://www.eliteprospects.com/player/12345/john-doe'
                                    }],
                }
            }
        }]
    },
    'Future considerations': {
        'Id' : np.random.randint(1, 100000)

    }}

   

with add_trade:
    st.write("Add Trade")
    
    n_assets = st.slider('Number of assets involved in trade', min_value=2, max_value=30, value=2)

    for i in range(n_assets):
        st.write(f"Asset #{i+1}")
        asset_id = st.text_input(f'Asset ID {i}', key=f'asset_id_{i}')
        asset_type = st.selectbox(f'Asset Type {i}', ['Player', 'Draft Pick', 'Future considerations'], key=f'asset_type_{i}')
        
        team_from = st.selectbox(f'Team from {i}', teams, key=f'team_from_{i}')
        team_to = st.selectbox(f'Team to {i}', teams, key=f'team_to_{i+1}')  # Updated key to avoid duplication
        
        details = st.text_area(f'Details {i}', key=f'details_{i}')
        
        st.write('---')

        # Example of how to add data to a dictionary
        data = {
            'AssetId': asset_id,
            'Asset_details': asset_maping_json[asset_type],  # Assuming asset_mapping_json is defined somewhere
            'Team_from': team_from,
            'Team_to': team_to,
            'Details': details
        }

        st.write(data)


with edit_trade:
    st.write("Edit Trade")
    # Add separator
    st.markdown('---')
    st.write("Edit Trade")

with delete_trade:
    st.write("Delete Trade")
    # Add separator
    st.markdown('---')
    st.write("Delete Trade")