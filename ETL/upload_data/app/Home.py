import streamlit as st
import pandas as pd
import numpy as np


# Make home page
# Add page name and logo
hockey_emoji_png = "https://em-content.zobj.net/source/apple/81/ice-hockey-stick-and-puck_1f3d2.png"
st.set_page_config(page_title='NHL Trade market', page_icon=hockey_emoji_png,
                   layout="centered", initial_sidebar_state='auto',
                   menu_items={'Get help': 'https://www.streamlit.io/'},)

st.sidebar.title('NHL Trade market')
st.sidebar.image(hockey_emoji_png, width=100)
st.sidebar.markdown('Understanding the NHL Trade market')
st.sidebar.markdown('*Powered by [EliteProspects](https://www.eliteprospects.com)*')
st.sidebar.markdown('---')



# Title
st.title('NHL Trade market')

st.subheader('Understanding the NHL Trade market')



# Add separator
st.markdown('---')

# Add in development message
st.info('This app is still in development. Please check back later for more features.')


#Add tabs
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(["Trades",
                            "Signings",
                            "Recalls/Sent down/Waivers",
                            "Buyouts/Terminations/Retirements",
                            "Suspensions",
                            "Injuries",
                            "Drafts",
                            "Others"])



    

