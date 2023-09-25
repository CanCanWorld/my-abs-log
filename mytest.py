import pymongo

if __name__ == '__main__':
    client = pymongo.MongoClient("localhost", 27017)
    for name in client.list_database_names():
        print(name)
