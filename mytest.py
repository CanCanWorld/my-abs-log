import pymongo

# if __name__ == '__main__':
#     client = pymongo.MongoClient("localhost", 27017)
#     print(client)
#     for name in client.list_database_names():
#         print(name)
#     log_db = client["log_db"]
#     registry = log_db["registry"]
#     line = {'server': 'DESKTOP-12C66JE', 'inode': 'test_inode', 'offset': 'test_offset'}
#     registry.insert_one(line)
    # registry.find({'server': server}, {'server': 1, 'inode': 1, 'offset': 1})
