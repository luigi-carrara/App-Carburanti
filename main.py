# This is a sample Python script.

# Press Maiusc+F10 to execute it or replace it with your code.
# Press Double Shift to search everywhere for classes, files, tool windows, actions, and settings.


class Car:

    id = ''
    name = ''
    maxSpeed = 0
    team = ''

    #Costruttore classe Car
    def __init__(self,id,name):
        self.id = id;
        self.maxSpeed= name;


class Track:
    name = ''
    distance = 0
    numTruns = 0
    location = ''

class Driver:

    id = ''
    name = ''
    age = 0
    number = 0
    team = ''
    points = 0
    car = ''

class Championship:

    id = ''
    name = ''
    numRaces = 0
    races = 0
    teams = []
    prize = 0

class Race:

    id = ''
    championship = ''
    numLaps = 0
    teams = []

class Team:
    id = ''
    name = ''
    drivers = []
    points = 0
    cars = []


class LeaderBoard:
    pass
def print_hi(name):
    # Use a breakpoint in the code line below to debug your script.
    print(f'Hi, {name}')  # Press Ctrl+F8 to toggle the breakpoint.


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    print_hi('PyCharm')

# See PyCharm help at https://www.jetbrains.com/help/pycharm/
