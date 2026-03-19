"""
Sentinel Interface Library


Has pre-defined scripts for common Sentinel operations, such as:
- Making API calls
- Connecting to devices
- Making phone calls through Twilio
- Scraping the web for information
- Calling smaller models to assist with tasks
- And more!

This library will be imported into the main Sentinel script, allowing it to easily access these functions and perform a wide range of tasks without needing to write new code for each operation.

The cycle will go as follows:

Goal -> Plan (determine tools needed) -> Create/Use Tools -> Execute Plan -> Repeat until Goal is achieved

A larger model from groq may be used to determine the plan and which tools to use, while smaller models can be used to execute specific tasks within the plan.
"""


# Import necessary libraries

# For making API calls
import requests
import json
import groq

# For connecting to devices

import paramiko

# For making phone calls through Twilio
from twilio.rest import Client

# For web scraping
from bs4 import BeautifulSoup
import re
import time
import random
import os
from dotenv import load_dotenv

# Load environment variables from .env file

load_dotenv()



