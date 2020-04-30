from flask import Flask, request
import requests
from twilio.twiml.messaging_response import MessagingResponse
from geopy.geocoders import Nominatim
import constants
import json

# TODO: Type *national* to get the latest country-wide Covid-19 stats.
# TODO: Get district-wise stats
# TODO: Get distance from your location to nearest active case in your city, district, or state
# TODO: Get category wise list of essential services near your location
# TODO: create a mapping of state, city, and district name aliases
# TODO: When incoming message is Resources or Cases send default message as:
#       Your location is set to <the last set location>. To change it, send your location again


app = Flask(__name__)

geolocator = Nominatim(user_agent="covid_bot", timeout=5)


@app.route('/bot', methods=['POST'])
def bot():
    incoming_values = request.values
    print(incoming_values)

    latitude = incoming_values.get('Latitude',  '')
    longitude = incoming_values.get('Longitude', '')
    # geolocator API expects coordinates as a single comma separated string of latitude and longitude
    geo_coordinates_string = " ,".join((latitude, longitude))

    incoming_msg = incoming_values.get('Body', '').lower()
    resp = MessagingResponse()
    msg = resp.message()
    responded = False

    national_api = 'https://api.covid19india.org/data.json'
    response = get_response(national_api)
    statewise_data_list = response.get('statewise')
    state_names = [each["state"].lower() for each in statewise_data_list]

    district_api = 'https://api.covid19india.org/v2/state_district_wise.json'
    states_with_district_list = get_response(district_api)  # list with each element a dict with key "state"

    welcome_message = f'''
Hi there! I am a bot that gives you the latest information on Covid-19 from India.
-Type *Total* to get the latest country-wide Covid-19 stats.
-Type the exact name of a state to get it's latest Covid-19 stats.
-Send your location to get the latest stats from your district along with essential services available in your region.
-Type *help* anytime to to learn how to interact with me.
'''

    help_message = f'''
Say *hi* to begin an interaction with me anytime.
-Type *Total* to get the latest country-wide Covid-19 stats.
-Type the exact name of a state to get it's latest Covid-19 stats.
-Send your location to get the latest stats from your district along with essential services available in your region.
'''

    fallback_message = 'Sorry, I did not quite get that. Type *help* to learn how to interact with me.'

    greeting_tokens = ['hi', 'hello', 'hey']
    if incoming_msg in greeting_tokens:
        # return greeting message
        msg.body(welcome_message)
        responded = True

    if incoming_msg in state_names:
        # return stats
        state = incoming_msg
        i = state_names.index(state)
        statewise_data = statewise_data_list[i]
        statewise_data_message = get_statewise_data_message(state, statewise_data)
        msg.body(statewise_data_message)
        responded = True

    if 'help' in incoming_msg:
        # return help message
        msg.body(help_message)
        responded = True

    if latitude:
        # TODO: Replace temporary file operation with nosql DB like mongoDB
        geo_location_dict = get_reverse_geocode(geo_coordinates_string)  # tuple of city, state
        location_message = get_location_message(geo_location_dict)
        msg.body(location_message)
        # save geo_location_dict with MessageSID on a temporary file
        with open('temp.json', 'w') as fp:
            json.dump({"address": geo_location_dict}, fp)
        responded = True

    if 'cases' in incoming_msg:
        with open('temp.json') as json_data:
            geo_location_dict = json.load(json_data).get("address", {})
            print(geo_location_dict)
        district = geo_location_dict.get('state_district', '')  # district is not lowercase
        state = geo_location_dict.get('state', '')  # state is not lowercase
        district = district.replace(district, constants.districts.get(district, district))
        district_data = get_district_data(states_with_district_list, district, state)
        district_data_message = get_district_data_message(district_data)
        msg.body(district_data_message)
        responded = True

    if 'services' in incoming_msg:
        # TODO: Get services from nearest city or district or state or PAN India
        # some cities are named differently in the resources.json API, eg Delhi
        # if a city is not found, set district city as the city in resources.json
        # if district city not found in resources.json, get nearest city from resources.json in the state
        # after getting a city show categories which includes pan country and pan state as category too?
        # PAN India is a state and PAN State is a city as filter in resources.json
        # if a city/district is not found as a city in resources.json, the city will be set as PAN State
        # if a state is not found in resources.json the state will be as PAN state
        with open('temp.json') as json_data:
            geo_location_dict = json.load(json_data).get("address", {})
            print(geo_location_dict)

        services_api = 'https://api.covid19india.org/resources/resources.json'
        services_list = get_response(services_api).get('resources', [])

        state = geo_location_dict.get('state', '')
        state_in_resources = state.replace(state, constants.states_from_resources.get(state, state))
        services_list_by_state = get_essential_services(services_list, "state", state_in_resources)
        # if services_list_by_state is [], it means state not found in resources.json. Hence, use state="PAN India"
        # in that case, mention "We don't have any information on this state, getting information from PAN India
        # pan india also has city as pan state, so no need to filter by city again

        city = geo_location_dict.get('city', '')
        if not city:  # city is not found in geo_location_dict, eg location is a village
            city = geo_location_dict.get('state_district', '')
        city_in_resources = city.replace(city, constants.districts.get(city, city))
        if services_list_by_state:  # non empty
            if city_in_resources in [each['city'] for each in services_list_by_state]:
                services_list_by_city = get_essential_services(services_list_by_state, "city", city_in_resources)
                services_dict_by_category = get_services_by_category(services_list_by_city)
                services_keys = [each for each in services_dict_by_category.keys()]
                context = {"flow": "choose_services",
                           "services": services_dict_by_category,
                           "keys": services_keys,
                           "location": city_in_resources,
                           "location_type": "city_in_resources"}
                with open('temp.json', 'w') as fp:
                    json.dump({"address": geo_location_dict, "context": context}, fp)
                services_menu = get_services_menu(services_keys, city_in_resources)
                msg.body(services_menu)
                responded = True

            else:  # city not found in essential services list for the given state
                context = {"flow": "choose_service_location",
                           "services": services_list_by_state,
                           "location": state_in_resources,
                           "location_type": "state_in_resources"
                           }
                with open('temp.json', 'w') as fp:
                    json.dump({"address": geo_location_dict, "context": context}, fp)
                msg.body("Sorry, we don't have any information about essential services in your City/location."
                         "Reply with the number corresponding to the location to find any services from:"
                         "Choose from nearby city or PAN State by replying the number"
                         "1. Nearest City"
                         "2. PAN State")
                responded = True

        else:  # if services_list_by_state is empty, meaning state isn't in resources, hence choose PAN India resources
            services_list_by_state = get_essential_services(services_list, "state", "PAN India")
            services_list_by_city = get_essential_services(services_list_by_state, "city", "PAN State")
            services_dict_by_category = get_services_by_category(services_list_by_city)  # get services as a dictionary
            # with key as service and value as a list of services with that key
            services_keys = services_dict_by_category.keys()
            context = {"flow": "choose_services",
                       "services": services_dict_by_category,
                       "keys": services_keys,
                       "location": "PAN India",
                       "location_type": "PAN India"}
            with open('temp.json', 'w') as fp:
                json.dump({"address": geo_location_dict, "context": context}, fp)
            services_menu = get_services_menu(services_keys, "PAN India")
            service_menu = "Sorry, we don't have any information about essential services in your State.\n" + services_menu
            msg.body(services_menu)
            responded = True

    if incoming_msg in constants.numeric_inputs:  # possible range of values for essential service options
        with open('temp.json') as json_data:
            geo_location_dict = json.load(json_data).get("address", {})
            print(geo_location_dict)
        with open('temp.json') as json_data:
            context = json.load(json_data).get("context", {})
            print(context)
        if context["flow"] == "choose_service_location":  # only this flow will update the context to set a city
            if incoming_msg == str(1):  # selecting nearest city from the state; display essential services menu for nearest city in resources
                pass
            elif incoming_msg == str(2): # displaying PAN state essential services; this is not services from all cities in the state
                pass
        if context["flow"] == "choose_services":
            key = context["keys"][int(incoming_msg)-1]
            services_list = context["services"][key]
            services_message = get_services_message(services_list, key, context["location"])
            msg.body(services_message)
            responded = True

    if not responded:
        msg.body('Sorry, I did not quite get that. Type *help* to learn how to interact with me.')

    return str(resp)


def get_response(url):
    response = requests.get(url)
    return response.json()


def get_statewise_data_message(state, data):
    active = data.get('active')
    confirmed = data.get('confirmed')
    recovered = data.get('recovered')
    deaths = data.get('deaths')
    delta_confirmed = data.get('deltaconfirmed')
    delta_deaths = data.get('deltadeaths')
    delta_recovered = data.get('deltarecovered')
    last_updated_timestamp = data.get('lastupdatedtime')

    data_message = f'''
*Latest Covid-19 data from {state.title()}*
*Total cases:*
Active: *{active}*
Confirmed: *{confirmed}*
Recovered: *{recovered}*
Deceased: *{deaths}*
*New cases:*
Confirmed: *{delta_confirmed}*
Recovered: *{delta_recovered}*
Deceased: *{delta_deaths}*

Last Updated: *{last_updated_timestamp}*

View more: https://www.covid19india.org/
'''
    return data_message


def get_geocode():
    pass


def get_reverse_geocode(coordinates):
    location = geolocator.reverse(coordinates, exactly_one=True)
    address_dict = location.raw['address']
    print(address_dict)
    return address_dict


def get_location_message(geo_location_dict):
    village = geo_location_dict.get('village', '')
    city = geo_location_dict.get('city', '')
    district = geo_location_dict.get('state_district', '')
    state = geo_location_dict.get('state', '')
    if city:
        address = ' ,'.join([city, district, state])
    elif village:
        address = ' ,'.join([village, district, state])
    else:
        address = ' ,'.join([district, state])
    location_message = f'''
Your detected location is {address}.
-Type *Cases* to get the lastest cases in your current District.
-Type *Services* to see the essential services available in your region.
'''
    return location_message


def get_district_data(states_with_district_list, district, state):
    for each in states_with_district_list:
        if state == each['state']:  # state is not lowercase
            for district_data in each['districtData']:
                if district == district_data['district']:  # district is not lowercase
                    return district_data


def get_district_data_message(data):
    active = data.get('active')
    confirmed = data.get('confirmed')
    recovered = data.get('recovered')
    deceased = data.get('deceased')
    delta_confirmed = data.get('delta').get('confirmed')
    delta_deceased = data.get('delta').get('deceased')
    delta_recovered = data.get('delta').get('recovered')

    data_message = f'''
*Latest Covid-19 data from {data.get('district')} (District)*
*Total cases:*
Active: *{active}*
Confirmed: *{confirmed}*
Recovered: *{recovered}*
Deceased: *{deceased}*
*New cases:*
Confirmed: *{delta_confirmed}*
Recovered: *{delta_recovered}*
Deceased: *{delta_deceased}*
'''
# Type *Services* to see the essential services available in your region.
    return data_message


def get_nearest_city_from_resources(services_list_by_state, state, city):
    cities = [each["city"] for each in services_list_by_state]
    nearest_city = get_nearest_city(cities, city)
    return nearest_city


def get_nearest_city(cities, city):
    pass


def get_closest_active_case(*args):
    # get district from city name
    pass


def get_services_by_category(services_list):
    services_dict = {}
    for service in services_list:
        key = service["category"]
        services = []
        services = [each for each in services_list if each["category"] == key]
        services_dict[key] = services
    return services_dict


def get_services_menu(services_keys, city):
    services_menu = '\n'.join([str(services_keys.index(each)+1)+". "+each for each in services_keys])
    services_menu = f'''
*Essential Services available in {city}*:
Reply with the number corresponding to each service to see available services in that category: 
{services_menu}
'''
    return services_menu


def get_services_message(services_list, key, location):
    services = []
    for service in services_list:
        phone_numbers = service["phonenumber"].split('\n')
        # each service is a dict, and we need to extract their values
        services.append('*'+service["nameoftheorganisation"]+'*'+'\nContact: '+'*'+service["contact"]+'*'+"\nPhone: "+'*'+', '.join(phone_numbers)+'*')
    services_message = '\n'.join(str(services.index(each)+1)+". "+each for each in services)
    services_message = f'''
Essential Services in the category *{key}* available in *{location}*: 
{services_message}

Reply with another number to view the corresponding services.

Visit https://www.covid19india.org/essentials for more.
'''
    print(services_message)
    return services_message


def get_essential_services(services_list, key, value):
    # if service_list is complete, key is "state" and value is state. return is statewise services list
    # if services_list is statewise, key is "city" and value is city name. return is citywise services list
    filtered_services_list = [each for each in services_list if each[key] == value]
    return filtered_services_list
####


if __name__ == '__main__':
    app.run()