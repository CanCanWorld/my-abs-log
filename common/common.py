# -*- coding:utf-8 -*-
import time
import re
import glob
import json
import pymongo
from sys import exit
from os import path
from functools import wraps
from urllib.parse import unquote
from datetime import datetime, timedelta

from config import LOG_FORMAT, LOG_TYPE, MONGO_HOST, MONGO_PORT, ABS_SPECIAL, LOG_PATH, EXCLUDE

mongo_client = pymongo.MongoClient(MONGO_HOST, MONGO_PORT, connect=False)
today = time.strftime('%Y%m%d', time.localtime())  # 今天日期


def timer(func):
    """测量函数执行时间的装饰器"""
    @wraps(func)
    def inner_func(*args, **kwargs):
        t0 = time.time()
        result_ = func(*args, **kwargs)
        t1 = time.time()
        print("Time running %s: %s seconds" % (func.__name__, str(t1 - t0)))
        return result_
    return inner_func


# -----log_analyse使用----- #
# 检查config.py中提供的日志格式中的必要字段
needed_fields = ('$remote_addr', [('$request_method', '$uri', '$args'), '$request', ('$request_method', '$request_uri')],
                 '$request_time', '$status', ['$body_bytes_sent', '$bytes_sent'], ['$time_local', '$time_iso8601'])
if LOG_TYPE == 'plaintext':
    supplied_fields = LOG_FORMAT.replace('[', '').replace(']', '').replace('"', '').split()
else:  # LOG_TYPE == 'json':
    supplied_fields = dict([(v, k) for k, v in json.loads(LOG_FORMAT).items()]).keys()
for field in needed_fields:
    if isinstance(field, str):
        if field not in supplied_fields:
            exit("Error: please confirm '{}' is in your log_format".format(field))
    if isinstance(field, list):
        if all([isinstance(x, str) for x in field]):
            if not any([x in supplied_fields for x in field]):
                exit("Error: please confirm one of '{}' is in your log_format".format(field))
        else:
            field_iter = (x for x in field)
            try:
                while True:
                    x = next(field_iter)
                    if isinstance(x, str):
                        if x in supplied_fields:
                            break
                    elif isinstance(x, tuple):
                        if all([y in supplied_fields for y in x]):
                            break
            except StopIteration:
                exit("Error: please confirm one of '{}' is in your log_format".format(field))


# 利用非贪婪匹配和分组匹配(plaintext格式需要)
ngx_style_log_field_pattern = {'$remote_addr': '(?P<remote_addr>.*?)',
                               '$time_local': '(?P<time_local>.*?)',
                               '$time_iso8601': '(?P<time_local>.*?)',
                               '$request': '(?P<request>.*?)',
                               '$request_method': '(?P<request_method>GET|POST|HEAD|DELETE|PUT|OPTIONS|CONNECT)',
                               '$status': '(?P<status>.*?)',
                               '$body_bytes_sent': '(?P<body_bytes_sent>.*?)',
                               '$bytes_sent': '(?P<body_bytes_sent>.*?)',
                               '$request_time': '(?P<request_time>.*?)',
                               '$http_referer': '(?P<http_referer>.*?)',
                               '$http_user_agent': '(?P<http_user_agent>.*?)',
                               '$http_x_forwarded_for': '(?P<http_x_forwarded_for>.*)',
                               '$scheme': '(?P<scheme>.*?)',
                               '$request_uri': '(?P<request_uri>.*?)',
                               '$uri': '(?P<uri>.*?)',
                               '$document_uri': '(?P<uri>.*?)',
                               '$args': '(?P<args>.*?)',
                               '$query_string': '(?P<args>.*?)',
                               '$server_protocol': '(?P<server_protocol>.*?)',
                               '$server_name': '(?P<http_host>.*?)',
                               '$host': '(?P<http_host>.*?)',
                               '$http_host': '(?P<http_host>.*?)',
                               '$request_length': '(?P<request_length>.*?)',
                               '$remote_user': '(?P<remote_user>.*?)',
                               '$gzip_ratio': '(?P<gzip_ratio>.*?)',
                               '$connection_requests': '(?P<connection_requests>.*?)',
                               '$connection': '(?P<connection>.*?)',
                               '$upstream_addr': '(?P<upstream_addr>.*?)',
                               '$upstream_status': '(?P<upstream_status>.*?)'
                               }
# 通过LOG_FORMAT得到可以匹配整行日志的log_pattern
for filed in supplied_fields:
    if filed in ngx_style_log_field_pattern:
        LOG_FORMAT = LOG_FORMAT.replace(filed, ngx_style_log_field_pattern[filed], 1)
log_pattern = LOG_FORMAT.replace('[', '\\[').replace(']', '\\]')
# $request的正则, 其实是由 "request_method request_uri server_protocol"三部分组成
request_uri_pattern = r'^(?P<request_method>GET|POST|HEAD|DELETE|PUT|OPTIONS|CONNECT) ' \
                      r'(?P<request_uri>.*?) ' \
                      r'(?P<server_protocol>.*)$'
log_pattern_obj = re.compile(log_pattern)
request_uri_pattern_obj = re.compile(request_uri_pattern)

# 文档中_id字段中需要的随机字符串
random_char = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
# 快速转换月份格式
month_dict = {'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06',
              'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'}


def todo_log():
    """通过配置文件取得要处理的日志文件"""
    all_find = glob.glob(LOG_PATH)
    return [one for one in all_find if path.basename(one) not in EXCLUDE]


def convert_time(t_value, t_type):
    """将nginx中time_local或time_iso8601格式的时间转换为201902250101格式
    返回元组 (date,hour,minute)"""
    if t_type == 'time_local':
        _time_local = t_value.split(':')
        _d_m_y = _time_local[0].split('/')
        date = _d_m_y[2] + month_dict[_d_m_y[1]] + _d_m_y[0]  # 将20/Feb/2019转为20190220格式
        return date, _time_local[1], _time_local[2]
    if t_type == 'time_iso8601':
        _time_local = t_value.split('T')
        _date = _time_local[0].split('-')
        _time = _time_local[1].split(':')
        return ''.join(_date), _time[0], _time[1]


def text_abstract(request_uri_=None, uri_=None, args_=None, log_name_=None):
    """
    对uri和args进行抽象化,利于分类
    默认规则:
        uri中若 两个'/'之间 或 '/'和'.'之间仅由"0-9或-或_"组成,则将其抽象为'*'
        args中所有参数的值抽象为'*'
    text: 待处理的内容
    site: 站点名称
    """
    if request_uri_:
        uri_args = request_uri_.split('?', 1)
        uri = unquote(uri_args[0])
        args = '' if len(uri_args) == 1 else unquote(uri_args[1])
    else:
        uri = unquote(uri_)
        args = unquote(args_)
    # 特殊抽象规则
    if log_name_ in ABS_SPECIAL:
        for uri_pattern in ABS_SPECIAL[log_name_]:
            if re.search(uri_pattern, uri):
                if 'uri_replace' in ABS_SPECIAL[log_name_][uri_pattern]:
                    uri = re.sub(uri_pattern, ABS_SPECIAL[log_name_][uri_pattern]['uri_replace'], uri)
                if 'arg_replace' in ABS_SPECIAL[log_name_][uri_pattern]:
                    for arg_pattern in ABS_SPECIAL[log_name_][uri_pattern]['arg_replace']:
                        if re.search(arg_pattern, args):
                            args = re.sub(arg_pattern, ABS_SPECIAL[log_name_][uri_pattern]['arg_replace'][arg_pattern], args)
                        else:
                            args = re.sub('=[^&=]+', '=*', args)
                return uri, args
    # uri默认抽象规则(耗时仅为原逻辑的1/3)
    for i in re.findall('/[0-9_-]+(?=[/.]|$)', uri):
        uri = uri.replace(i, '/*', 1)
    return uri, re.sub('=[^&=]+', '=*', args)


def get_median(sorted_data):
    """获取列表的中位数"""
    half = len(sorted_data) // 2
    return (sorted_data[half] + sorted_data[~half]) / 2


def get_quartile(data):
    """获取列表的4分位数(参考盒须图思想,用于体现响应时间和响应大小的分布.)
    以及min和max值(放到这里主要考虑对排序后数据的尽可能利用)"""
    data = sorted(data)
    size = len(data)
    if size == 1:
        return data[0], data[0], data[0], data[0], data[0]
    half = size // 2
    q1 = get_median(data[:half])
    q2 = get_median(data)
    q3 = get_median(data[half + 1:]) if size % 2 == 1 else get_median(data[half:])
    return data[0], q1, q2, q3, data[-1]


def special_insert_list(arr, v):
    """list插入过程加入对最大值(index: -1)的维护"""
    if len(arr) > 0:
        if v >= arr[-1]:
            arr.append(v)
        else:
            arr.insert(-1, v)
    else:
        arr.append(v)


def special_update_dict(dict_obj, key, standby_value=None, sub_type=None, sub_keys=None, sub_values=None):
    """若key对应的value类型为非字典类型(数字,字符,列表,元组), 对应第1,2,3个参数
    若key对应的value类型为字典,对应第1,2,4,5,6参数"""
    if sub_type is None:
        if key in dict_obj:
            dict_obj[key] += standby_value
        else:
            dict_obj[key] = standby_value
    elif isinstance(sub_type, dict):
        if key in dict_obj:
            for k, v in zip(sub_keys, sub_values):
                dict_obj[key][k] += v
        else:
            dict_obj[key] = sub_type
            for k, v in zip(sub_keys, sub_values):
                dict_obj[key][k] = v


def get_delta_date(date, delta):
    """对于给定的date(格式: 20180315), 返回其往前推delta天的日期(相同格式)"""
    year = int(date[0:4])
    month = int(date[4:6])
    day = int(date[6:8])
    min_date = datetime(year, month, day) - timedelta(days=delta-1)
    return min_date.strftime('%Y%m%d')


# -----log_show使用----- #
def get_human_size(n):
    """返回更可读的size单位"""
    units = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB'}
    i = 0
    while n//1024 > 0 and i < 3:
        n = n/1024
        i += 1
    return format(n, '.2f') + ' ' + units[i]


def match_condition(server, start, end, uri_abs=None, args_abs=None, ip=None, error_code=None):
    """根据指定条件返回mongodb中aggregate操作pipeline中的$match
    用$and操作符，方便对$match条件进行增减
    server: 显示来自该server的日志
    start: 开始时间
    end: 结束时间
    uri_abs: 经过抽象的uri
    args_abs: 经过抽象的args
    ip: 指定的ip地址
    error_code: 指定的错误码"""
    if start and end:
        basic_match = {'$match': {'$and': [{'_id': {'$gte': start}}, {'_id': {'$lt': end}}]}}
    elif start and not end:
        basic_match = {'$match': {'$and': [{'_id': {'$gte': start}}]}}
    elif end and not start:
        basic_match = {'$match': {'$and': [{'_id': {'$lt': end}}]}}
    else:  # 默认取今天的数据做汇总
        basic_match = {'$match': {'$and': [{'_id': {'$gte': today}}]}}
    if server:
        basic_match['$match']['$and'].append({'_id': {'$regex': server + '$'}})
    special_match = {'$match': {}}
    if uri_abs:
        special_match['$match']['$and'] = [{'requests.uri_abs': uri_abs}]
    if args_abs:
        special_match['$match']['$and'].append({'requests.args.args_abs': args_abs})
    if ip:
        special_match['$match']['$and'] = [{'requests.ips.ip': ip}]
    if error_code:
        special_match['$match']['$and'] = [{'requests.errors.error_code': error_code}]
    return {'basic_match': basic_match, 'special_match': special_match}


def total_info(mongo_col, match, project={'$match': {}}, uri_abs=None, args_abs=None, ip=None, error_code=None):
    """返回指定条件内的hits/bytes/time总量
    mongo_col: 本次操作对应的集合名称
    match: pipeline中的match条件(match_condition由函数返回，包含两部分$match)
    project: pipeline中的$project(目前detail子命令需要),默认值仅用于占位
    """
    pipeline = [match['basic_match'], project,
                {'$group': {'_id': 'null', 'total_hits': {'$sum': '$total_hits'}, 'total_bytes': {'$sum': '$total_bytes'},
                            'total_time': {'$sum': '$total_time'}, 'invalid_hits': {'$sum': '$invalid_hits'}, 'error_hits': {'$sum': '$error_hits'}}}]
    if uri_abs and args_abs:
        pipeline.insert(2, {'$unwind': '$requests'})
        pipeline.insert(3, {'$unwind': '$requests.args'})
        pipeline.insert(4, match['special_match'])
        pipeline[-1]['$group']['total_hits']['$sum'] = '$requests.args.hits'
        pipeline[-1]['$group']['total_bytes']['$sum'] = '$requests.args.bytes'
        pipeline[-1]['$group']['total_time']['$sum'] = '$requests.args.time'
    elif uri_abs:
        pipeline.insert(2, {'$unwind': '$requests'})
        pipeline.insert(3, match['special_match'])
        pipeline[-1]['$group']['total_hits']['$sum'] = '$requests.hits'
        pipeline[-1]['$group']['total_bytes']['$sum'] = '$requests.bytes'
        pipeline[-1]['$group']['total_time']['$sum'] = '$requests.time'
    elif ip:
        pipeline.insert(2, {'$unwind': '$requests'})
        pipeline.insert(3, {'$unwind': '$requests.ips'})
        pipeline.insert(4, match['special_match'])
        pipeline[-1]['$group']['total_hits']['$sum'] = '$requests.ips.hits'
        pipeline[-1]['$group']['total_bytes']['$sum'] = '$requests.ips.bytes'
        pipeline[-1]['$group']['total_time']['$sum'] = '$requests.ips.time'
    elif error_code:
        pipeline.insert(2, {'$unwind': '$requests'})
        pipeline.insert(3, {'$unwind': '$requests.errors'})
        pipeline.insert(4, match['special_match'])
        pipeline[-1]['$group']['total_hits']['$sum'] = '$requests.errors.hits'
        pipeline[-1]['$group']['total_bytes']['$sum'] = '$requests.errors.bytes'
        pipeline[-1]['$group']['total_time']['$sum'] = '$requests.errors.time'
    try:
        # 符合条件的总hits/bytes/time/invalid_hits
        return mongo_col.aggregate(pipeline).next()
    except StopIteration:
        exit('Warning: no record under the condition you specified')


def group_by_func(arg):
    """根据指定的汇总粒度, 决定 aggregate 操作中 $group 条件的 _id 列"""
    if arg == 'minute':
        group_id = {'$substrBytes': ['$_id', 0, 12]}
    elif arg == 'ten_min':
        group_id = {'$substrBytes': ['$_id', 0, 11]}
    elif arg == 'day':
        group_id = {'$substrBytes': ['$_id', 0, 8]}
    else:  # 默认 arg == 'hour'
        group_id = {'$substrBytes': ['$_id', 0, 10]}
    return group_id


