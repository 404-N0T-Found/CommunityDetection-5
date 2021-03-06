# -*- coding: utf-8 -*-#

# -------------------------------------------------------------------------------
# https://blog.csdn.net/qq_40587374/article/details/86597293(数据集图)
# 其中dolphins的数据图比真实的的数据集标签多1
# Author:       liuligang
# Date:         2020/9/14
# -------------------------------------------------------------------------------


import math
import os
import shutil
import random
import time
from collections import defaultdict

import networkx as nx
import numpy as np

from my_generate_param import *
from my_show_result import show_result_image
from my_objects import MyResultInof, AlgorithmParam, NodeInfo, ShowResultImageParm
from my_util import timefn, need_show_data, print_result, trans_community_nodes_to_str
from my_util import transfer_2_gml, path, run_platform, calculate_params, add_result_to_mysql
from my_evaluation import generate_network

# 1) Distance function
a = 0.1  # 计算cc(i,j)的时候使用的，一个较小的正值，避免分母为0的情况
b = 0.1  # 计算dist(i, j)的时候使用的，表示当i,j时孤立的节点的时候
# c = 0.8  # 在second_step()分配重叠节点的时候使用的。 todo 11.10 这个在论文后面的实验中有作对比
dc = 0.2  # todo dc取多少？论文中是当dc取2%效果最佳，因为这个直接影响到计算node_p的值
u = 0.1
G = nx.Graph()
node_outgoing_weight_dict = {}
node_knn_neighbors_dict = {}
node_influence_dict = {}
dist_martix = None
ls_martix = None
all_nodes_info_list = []


# path = path


# 计算G中最大权重
def calculate_maxw(need=False):
    if not need:
        return 1.0
    res = 0.0
    for u, v in G.edges:
        res = max(res, G[u][v]['weight'])
    return res


# 计算cc(i, j)，表示的是节点i,j的共同节点对节点i和节点j的链接强度的贡献，因此这个方法应该是考虑的节点的共同邻居节点
def calculate_cc_ij(nodei, nodej, V_ij=None, maxw=1.0):
    if V_ij is None:
        V_ij = nx.common_neighbors(G, nodei, nodej)
    t = 0.2  # todo 11.09 后面的实验中，t的值是做了变化的，0.2是最佳的,我们后期也得做个对比
    r = 1.0  # 暂定1.0，todo 没有弄明白论文中的r所指的意思?????
    res = 0.0
    for node in V_ij:
        w_ipj = min(G[nodei][node]['weight'], G[nodej][node]['weight'])
        # 其实这里会发现，针对如果是无权重的图，temp就是等于0的
        temp = math.pow(((w_ipj - maxw) / (r * t + a)), 2)
        res = res + w_ipj * math.exp(-temp)
    return res


def calculate_node_outgoing_weight(node):
    res = 0.0
    for n in G.neighbors(node):
        res = res + G[node][n]['weight']
    return res


# 计算ls(i, j)，同时考虑直接链接权重和共同节点的共享，所以讲道理这个函数是考虑的cc(i,j)和i,j的之间的权重值
def calculate_ls_ij(nodei, nodej, maxw=1.0):
    V_ij = list(nx.common_neighbors(G, nodei, nodej))
    cc_ij = calculate_cc_ij(nodei, nodej, V_ij, maxw)
    # i,j之间有边连接
    if G.has_edge(nodei, nodej):
        A_ij = G[nodei][nodej]['weight']
    else:
        A_ij = 0.0
    # 表示i, j之间没有共同邻居, 这里不用管两个节点之前是否又共同邻居
    # if len(V_ij) == 0:
    #     return 0.0
    # I_i = calculate_node_outgoing_weight(nodei)
    I_i = node_outgoing_weight_dict[nodei]
    # I_j = calculate_node_outgoing_weight(nodej)
    I_j = node_outgoing_weight_dict[nodej]
    res = ((cc_ij + A_ij) * (len(V_ij) + 1)) / min(I_i, I_j)
    return res


# 计算节点i,j的distance
def calculate_dist_ij(nodei, nodej, maxw=1.0):
    # 判断两个节点中是否存在至少一个节点为孤立节点
    if G.degree(nodei) == 0 or G.degree(nodej) == 0:
        ls_ij = 0.0
    else:
        ls_ij = calculate_ls_ij(nodei, nodej, maxw)
    res = 1 / (ls_ij + b)
    return res, ls_ij


# 初始化所有的节点之间的距离
@timefn  # 统计一下该函数调用花费的时间
def init_dist_martix():
    n = len(G.nodes)
    # 这里需要注意一点，因为使用了二维数组存储节点之间的dist数据，所以节点必须以数字表示，
    # 并且节点的下标必须是以0或者1开始
    # 对于非数字的graph，需要map转换一下
    dist_martix = [[1 / b for i in range(n + 1)] for i in range(n + 1)]
    ls_martix = [[0 for i in range(n + 1)] for i in range(n + 1)]
    # a = np.zeros([n+1, n+1])
    nodes = sorted(list(G.nodes))
    maxw = calculate_maxw()
    for i in range(0, n):
        nodei = nodes[i]
        if G.degree(nodei) == 0:
            continue
        for j in range(i + 1, n):
            nodej = nodes[j]
            if dist_martix[nodei][nodej] == 1 / b:
                dist_ij, ls_ij = calculate_dist_ij(nodei, nodej, maxw)
                dist_martix[nodei][nodej] = dist_ij
                dist_martix[nodej][nodei] = dist_ij
                ls_martix[nodei][nodej] = ls_ij
                ls_martix[nodej][nodei] = ls_ij
    return dist_martix, ls_martix


# 求网络的平均度
def calculate_knn():
    sum = 0
    # 返回的是每个节点的度的情况
    node_degree_tuple = nx.degree(G)
    for _, degree in node_degree_tuple:
        sum += degree
    return int(sum / len(node_degree_tuple))


# 计算一个节点的knn的邻居节点的集合 todo 这个方法有很严重的歧义，中英文版的论文给的不一样
def calculate_node_knn_neighbor(nodei, knn):
    knn_nodes = nx.neighbors(G, nodei)
    # 我个人觉得这里不一定是邻居节点,应该是将所有的节点的dist进行排序，取最近的k个节点
    # knn_nodes = [node for node in G.nodes if node != nodei]
    # 得到节点的所有邻居节点之间的dist
    node_neighbors_dist_tuple_list = [(x, dist_martix[nodei][x]) for x in knn_nodes]
    # 对所有的邻居节点进行排序········································
    node_neighbors_dist_tuple_list = sorted(node_neighbors_dist_tuple_list, key=lambda x: x[1])
    # 找到最小的k个邻居节点
    res = []
    k = len(node_neighbors_dist_tuple_list)
    # 如果不够就取所有的
    if k < knn:
        knn = k
    for i in range(knn):
        nodej = node_neighbors_dist_tuple_list[i][0]
        res.append(nodej)
    return res


# 计算每个节点的揉
def calculate_nodep(node):
    # 找到最小的k个邻居节点，这里不按照论文的来，这里就是算所有邻居节点
    # knn_neighbors = calculate_node_knn_neighbor(node)
    # knn_neighbors = node_knn_neighbors_dict.get(node)
    knn_neighbors = list(G.neighbors(node))
    res = 0.0
    # 如果不够就取所有的
    for knn_neighbor in knn_neighbors:
        # a = float(dist_martix[node][knn_neighbor])
        temp = math.pow((float(dist_martix[node][knn_neighbor]) / dc), 2)
        res = res + math.exp(-temp)
    return res


def init_all_nodes_influence(w1, w2):
    degree_centrality_dict = nx.algorithms.degree_centrality(G)
    betweenness_centrality_dict = nx.algorithms.betweenness_centrality(G)
    closeness_centrality_dict = nx.algorithms.closeness_centrality(G)
    node_influence_dict = {}
    for node in G.nodes:
        node_influence = w1 * degree_centrality_dict[node] + \
                         w2 * betweenness_centrality_dict[node] + \
                         (1 - w1 - w2) * closeness_centrality_dict[node]
        node_influence_dict[node] = node_influence
    return node_influence_dict


# 初始化所有的节点的信息
@timefn
def init_all_nodes_info(node_g_weight=2):
    res = []
    all_node_p = []
    all_node_w = []
    # 初始化所有节点的影响力
    node_influence_dict = init_all_nodes_influence(0.4, 0.4)
    # 1) 初始化所有的
    for node in G.nodes:
        # node_p = calculate_nodep(node)
        node_p = node_influence_dict[node]
        # node_w = calculate_node_outgoing_weight(node)
        node_w = node_outgoing_weight_dict[node]
        t = NodeInfo()
        t.node = node
        t.node_p = node_p
        t.node_w = node_w
        res.append(t)
        all_node_p.append(node_p)
        all_node_w.append(node_w)

    # 2) 对揉进行归一化
    min_node_p = min(all_node_p)
    max_node_p = max(all_node_p)
    min_node_w = min(all_node_w)
    max_node_w = max(all_node_w)
    for node_info in res:
        node_p = node_info.node_p
        node_p_1 = (node_p - min_node_p) / (max_node_p - min_node_p)
        node_info.node_p_1 = node_p_1
        node_w = node_info.node_w
        node_w_1 = (node_w - min_node_w) / (max_node_w - min_node_w)
        node_info.node_w_1 = node_w_1

    # 3) 初始化所有节点的伽马
    # 计算每个节点的伽马函数，由于这个方法外部不会调用，就暂且定义在方法内部吧，问题不大！
    def calculate_node_g(nodei, node_list):
        if len(node_list) == 0:
            return 1.0 / b
        temp = []
        for nodej in node_list:
            temp.append(node_g_weight * dist_martix[nodei][nodej])
        return min(temp)

    # 按照所有节点的揉进行升序排序
    res = sorted(res, key=lambda x: x.node_p)
    all_node_g = []
    for i in range(len(res)):
        # 当揉为最大的时候，取最大的dist
        if i == len(res) - 1:
            res[i].node_g = max(all_node_g)
            all_node_g.append(res[i].node_g)
        else:
            node_info = res[i]
            node = node_info.node
            # 因为res是根据揉排好序的，所有i之后的所有节点对应的揉都是大于当前的, 这里应该是需要加上后面的if
            node_list = [res[x].node for x in range(i + 1, len(res)) if res[x].node_p > node_info.node_p]
            node_g = calculate_node_g(node, node_list)
            # todo 想不通为什么这里会有计算出node_g = 10.0的情况？？？？
            if node_g == 1.0 / b:
                node_g = res[i - 1].node_g
            all_node_g.append(node_g)
            node_info.node_g = node_g
    # 4) 对所有的节点的伽马进行归一化，并且求出r
    max_node_g = max(all_node_g)
    min_node_g = min(all_node_g)
    node_node_r_dict = {}
    for node_info in res:
        node_g = node_info.node_g
        node_g_1 = (node_g - min_node_g) / (max_node_g - min_node_g)
        node_info.node_g_1 = node_g_1
        # 且顺便计算出node_r
        node_r = node_info.node_p_1 * node_info.node_g_1
        node_info.node_r = node_r
        node_node_r_dict[node_info.node] = node_r
    return res, node_node_r_dict


# 打印一下初始化之后的节点的信息，所有节点按照p进行排序
def print_node_info():
    for node_info in all_nodes_info_list:
        print "节点： { %s } node_p_1的值: { %f } node_g_1的值：{ %f } node_r的值： { %f } " \
              % (node_info.node, node_info.node_p_1, node_info.node_g_1, node_info.node_r)


# 讲道理这里应该还需要过滤一些更不不可能成为clustering node的节点
def filter_corredpond_nodes(all_nodes_info_list):
    all_nodes_info_list = sorted(all_nodes_info_list, key=lambda x: x.node_p)
    count = int(0.8 * len(all_nodes_info_list))
    sum_node_p = 0.0
    for i in range(count):
        sum_node_p += all_nodes_info_list[i].node_p
    averge_eighty_percen_node_p = float(sum_node_p) / count

    sum_node_r = 0.0
    all_nodes_info_list = sorted(all_nodes_info_list, key=lambda x: x.node_r)
    for i in range(count):
        sum_node_r += all_nodes_info_list[i].node_r
    averge_eighty_percen_node_r = float(sum_node_r) / count

    filter_nodes_info_list = []
    for node_info in all_nodes_info_list:
        if node_info.node_p < averge_eighty_percen_node_p or node_info.node_r < averge_eighty_percen_node_r:
            pass
        else:
            filter_nodes_info_list.append(node_info)
            sum_node_r += node_info.node_r
    averge_node_r = sum_node_r / len(filter_nodes_info_list)
    return filter_nodes_info_list, averge_node_r


# 初始化所有的节点的node_dr信息，并返回最大的node_dr以及对应的index
def init_filter_nodes_dr(filter_nodes_info_list):
    # 第一个节点应该是没有node_dr的，所以从第二个节点开始
    for i in range(1, len(filter_nodes_info_list)):
        a = filter_nodes_info_list[i - 1]
        b = filter_nodes_info_list[i]
        node_dr = b.node_r - a.node_r
        b.node_dr = node_dr


# ================================================================================
# 以上的所有代码应该是初始化好了所有的节点的信息，
# 包括揉，伽马，还有d伽马等信息。那么讲道理下面的步骤就应该是
# 1) 自动计算中心节点
# 2) 将节点划分到对应的社区
# ================================================================================

# 得到一维的线性拟合的参数a和b
def calculate_predict_node_dr(node_info_list, node_index):
    list_x = []
    list_y = []
    for i in range(len(node_info_list)):
        node_info = node_info_list[i]
        list_x.append(i + 1)
        list_y.append(node_info.node_dr)
    z = np.polyfit(list_x, list_y, 1)
    return z[0] * node_index + z[1]


# list_x = [1, 2, 3, 4, 5, 6]
# list_y = [2.5, 3.51, 4.45, 5.52, 6.47, 7.51]
# print calculate_linear_fitting_number(list_x, list_y, 8)
# 可以在这一步打印出节点的一些信息，进行验证
# for node in all_nodes_info_list:
#     print node.node, node.node_r, node.node_dr

# 算法二的核心，自动计算出node center
@timefn
def select_center(node_info_list, averge_node_r):
    def calculate_max_node_dr(node_info_list):
        max_index = -1
        max_node_dr = -1
        for i in range(1, len(node_info_list)):
            node_info = node_info_list[i];
            t = node_info.node_dr
            if max_node_dr < t:
                max_node_dr = t
                max_index = i
        return max_node_dr, max_index

    res = -1
    # 这里的循环的过程不就会导致一种结果，那就是只要某个max_index是center，
    # 那么之后的所有节点不就肯定都是啦？？？
    # todo 论文上的重复逻辑没有看懂，不知道是不是我代码所写的这个意思，需要讨论一下？？？？
    while len(node_info_list) > 3:
        _, max_index = calculate_max_node_dr(node_info_list)
        temp_node_info = node_info_list[max_index]
        true_node_dr = temp_node_info.node_dr
        # 将所有的前面的进行拟合
        node_info_list = node_info_list[0:max_index]
        if len(node_info_list) < 3 or temp_node_info.node_dr < averge_node_r * 0.8:
            break
        predict_node_dr = calculate_predict_node_dr(node_info_list, max_index)
        # todo 这么定义和论文不一样，到时候一起讨论一下？？？？
        if 2 * (true_node_dr - predict_node_dr) > true_node_dr:
            res = max_index
        else:
            break
    return res


# 初始化所有的中心节点,因为后面的节点划分社区都需要用到这个
def init_center_node(filter_nodes_info_list_index, filter_nodes_info_list, all_nodes_info_dict):
    center_node_dict = {}
    comunity = 1
    # 因为从 filter_node_info_list_index 到最后都是中心节点
    for i in range(filter_nodes_info_list_index, len(filter_nodes_info_list)):
        filter_node_info = filter_nodes_info_list[i]
        node_info = all_nodes_info_dict.get(filter_node_info.node)
        node_info.is_center_node = True
        # 设置中心节点的社区，从编号1开始
        node_info.communities.append(comunity)
        # 将center_node的信息加入到center_node_list中，因为first_step会使用到该信息
        center_node_dict[node_info.node] = comunity
        comunity += 1
    return center_node_dict


# 统计一下该节点和所有的中心节点的值都是0的情况，因为这种节点是随意划分的，需要思考一个方法把这种节点也正确划分
# 这里除了到中心节点为0的情况，还有一种情况就是到所有的中心节点的距离同相同
def calculate_zeor_ls_with_center_node(center_nodes=[], all_nodes=[]):
    all_ls_zero_nodes = []
    # 统计到所有中心节点为0的情况,或者到所有中心节点的ls强度都相同的点
    for node in all_nodes:
        temp = []
        if node not in center_nodes:
            for center_node in center_nodes:
                temp.append(ls_martix[node][center_node])
            # 如果到所有中心节点为0，或者到所有中心节点的距离都为0的话，那么该节点就不能随意划分
            if len(temp) == 0:
                continue
            if max(temp) == 0 or (len(temp) != 0 and max(temp) - min(temp) == 0):
                all_ls_zero_nodes.append(node)
    return all_ls_zero_nodes


# 将一些与中心节点的ls距离都是0的值进行划分，不能随意简单的划分
def divide_ls_zero_node(node, all_nodes_info_list, node_community_dict, center_nodes_community):
    index = 0
    length = len(all_nodes_info_list)
    for i in range(0, length):
        if node == all_nodes_info_list[i].node:
            index = i
            break
    waiting_node_info = all_nodes_info_list[index]
    waiting_node = waiting_node_info.node
    waiting_node_p = waiting_node_info.node_p
    min_dist = 1000
    community = -1
    lg_node_p_list = []
    for i in range(index + 1, length):
        node_info = all_nodes_info_list[i]
        if node_info.node_p > waiting_node_p:
            lg_node_p_list.append(all_nodes_info_list[i])
    if len(lg_node_p_list) == 0:
        # 随意划分一个，但是这种情况几乎没有
        community = random.choice(center_nodes_community)
    else:
        # 先看它的邻居分配情况
        node_neighbors = nx.neighbors(G, node)
        node_neighbors_community_dict = {}
        for node_neighbor in node_neighbors:
            t = node_community_dict.get(node_neighbor, [-1])[0]
            if t != -1:
                if node_neighbors_community_dict.has_key(t):
                    node_neighbors_community_dict[t] = node_neighbors_community_dict[t] + ls_martix[node][node_neighbor]
                else:
                    node_neighbors_community_dict[t] = ls_martix[node][node_neighbor]
        temp = node_neighbors_community_dict.values()
        max_neighbor_ls = -1000
        for key, value in node_neighbors_community_dict.items():
            if value > max_neighbor_ls:
                max_neighbor_ls = value
                community = key
        # 如果根据邻居还得不出该节点应该划分的社区，那么就按照下面的这种方式进行划分

        if len(temp) != 0 and len(temp) != 1 and max(temp) == min(temp):
            for node_info in lg_node_p_list:
                if dist_martix[node_info.node][waiting_node] < min_dist:
                    min_dist = dist_martix[node_info.node][waiting_node]
                    if node_community_dict.has_key(node_info.node):
                        community = node_community_dict.get(node_info.node)[0]
    if community == -1:
        # 随意划分一个，但是这种情况几乎没有
        community = random.choice(center_nodes_community)
    waiting_node_info.communities = []
    waiting_node_info.communities.append(community)
    # 这个结构主要是下面判断一个节点是否为包络节点需要使用到，所以在这里返回出去
    node_community_dict[waiting_node] = [community]


# 第一步将所有的非中心节点进行划分
@timefn
def first_step(center_node_dict):
    # node_community_dict 就是记录所有的节点的划分的社区信息{}, 因为很多地方会使用到这个
    # node_community_dict = center_node_dict.copy()
    node_community_dict = defaultdict(list)
    for node in center_node_dict.keys():
        node_community_dict[node] = [center_node_dict[node]]
    ls_zero_nodes = calculate_zeor_ls_with_center_node(list(node_community_dict.keys()), list(G.nodes))

    for node_info in all_nodes_info_list:
        waiting_node = node_info.node
        # 1) 先将所有的非中心节点且不是到所有的中心节点都不是零的值先进行划分
        if not node_info.is_center_node and waiting_node not in ls_zero_nodes:
            community = -1
            min_dist = -1000000
            for node in center_node_dict.keys():
                node_ij_weight = 0.0
                if G.has_edge(waiting_node, node):
                    node_ij_weight = G[waiting_node][node]['weight']
                ls_ij = ls_martix[node_info.node][node] + node_ij_weight
                if ls_ij > min_dist:
                    community = center_node_dict.get(node)
                    min_dist = ls_ij
            node_info.communities = []
            node_info.communities.append(community)
            # 这个结构主要是下面判断一个节点是否为包络节点需要使用到，所以在这里返回出去
            node_community_dict[waiting_node] = [community]

    # 2) 将所有的零节点(也就是该节点到所有的中心节点都的强度都是0)划分，这一步也非常重要
    for ls_zeor_node in ls_zero_nodes:
        # 中心节点划分的社区
        center_nodes_community = list(center_node_dict.values())
        divide_ls_zero_node(ls_zeor_node, all_nodes_info_list, node_community_dict, center_nodes_community)
    return node_community_dict, ls_zero_nodes


def need_select_center_again(node_node_r_dict, nodes):
    nodes_r = [node_node_r_dict[node] for node in nodes]
    nodes_arr = np.var(nodes_r)
    # print nodes_arr
    return nodes_arr


# 计算每个节点的knn个邻居节点的ls的值之和
def calculate_node_knn_neighboor_ls(nodei, knn_node_neighbors, node_community_dict, comminity=None):
    res = 0.0
    for nodej in knn_node_neighbors:
        if comminity is None:
            res += ls_martix[nodei][nodej]
        else:
            if node_community_dict.get(nodej)[0] == comminity:
                res += ls_martix[nodei][nodej]
    return res


# 计算非包络节点的membership, 用于二次划分时将该节点划分到一个新的社区
def calculate_node_membership(nodei, node_community_dict):
    # 得到nodei的knn的邻居节点
    # nodei_knn = calculate_node_knn_neighbor(nodei)
    nodei_knn = node_knn_neighbors_dict[nodei]
    # 得到nodei的knn个邻居节点以及它们的划分社区信息
    # node_knn_node_to_community_dict = [{node: node_community_dict.get(node)} for node in nodei_knn_neighbors]
    node_knn_community_to_node_dict = {}
    for nodej in nodei_knn:
        nodej_community = node_community_dict.get(nodej)[0]
        if node_knn_community_to_node_dict.has_key(nodej_community):
            node_knn_community_to_node_dict.get(nodej_community).append(nodej)
        else:
            node_knn_community_to_node_dict[nodej_community] = [nodej]
    node_membership_dict = {}
    # 对于每一个接待你进行划分
    for community_c in node_knn_community_to_node_dict.keys():
        res = 0.0
        node_knn_c = node_knn_community_to_node_dict.get(community_c)
        for nodej in node_knn_c:
            # nodej_knn = calculate_node_knn_neighbor(nodej)
            nodej_knn = node_knn_neighbors_dict[nodej]
            a = calculate_node_knn_neighboor_ls(nodej, nodej_knn, node_community_dict, community_c)
            b = calculate_node_knn_neighboor_ls(nodej, nodej_knn, node_community_dict)
            res += ls_martix[nodei][nodej] * (a / b)
        # 更新结果
        node_membership_dict[community_c] = res
    return node_membership_dict


# 划分重叠节点出来
@timefn
def second_step(node_community_dict, c, enveloped_weight=0.5, overlapping_candidates=[]):
    not_enveloped_nodes = []
    for node_info in all_nodes_info_list:
        nodei = node_info.node
        if not node_info.is_center_node:
            # 计算该节点是否为包络节点
            node_neighbors = list(nx.neighbors(G, nodei))
            community = node_info.communities[0]
            # 统计一下它的所有邻居节点和自身在同一个社区的情况
            same_community_sum = 0
            for node_neighbor in node_neighbors:
                # 在没有进行seconde_step之前，node_community_dict中的节点对应划分的社区应该是只有一个的
                node_community = node_community_dict.get(node_neighbor)[0]
                if node_community == community:
                    same_community_sum += 1
            # 说明改节点和周围的所有节点在一个社区中,或者它和它的邻居有一半的社区是相同的(todo 11.10 后面这一点是我添加的)
            if nodei not in overlapping_candidates:
                pass
            else:
                # 说明该节点就不是包络节点
                node_info.is_enveloped_node = False
            if same_community_sum == len(node_neighbors) or \
                    same_community_sum >= len(node_neighbors) * enveloped_weight:
                pass
            else:
                # 说明该节点就不是包络节点
                # node_info.is_enveloped_node = False
                not_enveloped_nodes.append(node_info.node)
            # 如果不是包络节点，那么会进行二次划分
            if not node_info.is_enveloped_node:
                # 1) 如果该节点和它的所有邻居划分社区都不相同，那么该节点先不管 # todo 论文中归感觉没有考虑这一点
                # 说明该节点和所有的邻居节点的社区中不包含该节点划分的社区，这种情况不管
                # nodei_knn_neighbors = calculate_node_knn_neighbor(nodei)
                nodei_knn_neighbors = node_knn_neighbors_dict[nodei]
                # 得到该节点的knn个最近的邻居节点的所有社区信息
                node_knn_neighbors_community = set([node_community_dict.get(node)[0] for node in nodei_knn_neighbors])
                # 表示的是该节点划分社区和周边所有的邻居划分的社区都不同，对于这种节点我们暂且不把它作为重叠节点处理
                if community not in node_knn_neighbors_community:
                    pass
                else:
                    node_membership_dict = calculate_node_membership(nodei, node_community_dict)
                    # 遍历所有的knn节点的membership值，判断该节点是否划分到多个社区
                    nodei_community = node_community_dict.get(nodei)[0]
                    nodei_membership = node_membership_dict.get(nodei_community)
                    node_membership_dict.pop(nodei_community)
                    for community_c in node_membership_dict:
                        if nodei_membership == 0.0:
                            # todo 这里会存在nodei_membership为0的情况，到时候再排查一下原因，先暂定跳过
                            break
                        t = node_membership_dict.get(community_c) / nodei_membership
                        if (t >= c):
                            # 说明需要将该节点划分到对应的社区
                            node_info.communities.append(community_c)
                            # 更新一下node_community_dict，说明该节点是一个重叠节点
                            if community_c != nodei_community:
                                node_community_dict.get(nodei).append(community_c)
        else:
            pass
    return not_enveloped_nodes


# 统计一下算法发现的重叠节点和真实的重叠节点的匹配的节点信息
def overlapping_mapping_sum(a=[], b=[]):
    if len(a) == 0 or len(b) == 0:
        return []
    mapping_overlapping_nodes = []
    for node in a:
        if node in b:
            mapping_overlapping_nodes.append(node)
    return mapping_overlapping_nodes


# 处理算法发现的结果(主要是直接将结果写入文件中，方便直接计算onmi，避免每次手动复制文件执行相应的脚本计算)
def handle_result_to_txt(all_nodes_info_list, not_overlapping_community_dict):
    community_nodes_dict = {}
    not_overlapping_community_node_dict = {}
    for node, communities in not_overlapping_community_dict.items():
        community = communities[0]
        if not_overlapping_community_node_dict.has_key(community):
            not_overlapping_community_node_dict.get(community).append(node)
        else:
            not_overlapping_community_node_dict[community] = [node]

    for node_info in all_nodes_info_list:
        node = node_info.node
        communities = node_info.communities
        for community in communities:
            if community_nodes_dict.has_key(community):
                community_nodes_dict.get(community).append(node)
            else:
                community_nodes_dict[community] = [node]

    # 将结果集合写入文件, 讲道理这里还应该将划分的非重叠社区的结果也划分进去，后面如果想统计非重叠的NMI的值也方便(以后再说吧！)
    file_path = path + "/lfr_code.txt"
    if os.path.exists(file_path):
        os.remove(file_path)
        print "delete lfr_code.txt success...."
    file_handle = open(file_path, mode="w")
    for key, value in community_nodes_dict.items():
        s = trans_community_nodes_to_str(value)
        file_handle.write(s + "\n")
    print "generate lfr_code.txt again...."
    return community_nodes_dict, not_overlapping_community_node_dict


# 统计一下算法发现的重叠节点，以及每个重叠节点所属的社区个数
def calculate_overlapping_nodes(node_community_dict):
    find_overlapping_nodes_dict = {}
    # 记录一下重叠节点被划分到的最多的社区个数和最小的社区个数
    min_om = 10000
    max_om = -10000
    for node in node_community_dict.keys():
        communites = len(node_community_dict[node])
        if communites >= 2:
            if communites < min_om:
                min_om = communites
            if communites > max_om:
                max_om = communites
            # 记录每个重叠节点被划分到了几个社区
            find_overlapping_nodes_dict[node] = communites
    return find_overlapping_nodes_dict, min_om, max_om


# 就统计一下按照node_p 和 node_r 排序之后的节点信息，可能debug的时候用一下(不重要)
def calculate_ascending_nodes(filter_nodes_info_list, all_nodes_info_list):
    # 这个保存一下所有节点按照node_r进行排序之后的节点编号的变化信息，只是用来清晰的记录那个节点的揉的值是最大的而已
    ascending_nod_r_nodes = []
    # 因为此时的所有的all_nodes_info_list 是按照node_p进行升序的
    for node_info in filter_nodes_info_list:
        ascending_nod_r_nodes.append(node_info.node)
    # 这个保存一下所有节点按照揉进行排序之后的节点编号的变化信息，只是用来清晰的记录那个节点的揉的值是最大的而已
    ascending_nod_p_nodes = []
    # 因为此时的所有的all_nodes_info_list 是按照node_p进行升序的,这里暂且只收集前百分之
    for i in range(len(all_nodes_info_list) - len(filter_nodes_info_list), len(all_nodes_info_list)):
        ascending_nod_p_nodes.append(all_nodes_info_list[i].node)
    return ascending_nod_p_nodes, ascending_nod_r_nodes


def calculate_node_KN(NB_i, nodei):
    t = 0
    node_KN_dict = defaultdict(list)
    while len(NB_i) > 0 and t < 2:
        NB_i_dict = defaultdict(list)
        for node in NB_i:
            NB_i_dict[node] = list(nx.common_neighbors(G, node, nodei))
        ni_kN = None
        ni_kN_size = -100
        for key, value in NB_i_dict.items():
            if len(value) > ni_kN_size:
                ni_kN = key
                ni_kN_size = len(value)
        gi_KN = NB_i_dict.get(ni_kN)
        gi_KN.append(ni_kN)
        NB_i = [node for node in NB_i if node not in gi_KN]
        t += 1
        node_KN_dict[ni_kN] = gi_KN
    return node_KN_dict


def calculate_L(G1_KN, G2_KN):
    l = 0
    for nodei in G1_KN:
        for nodej in G2_KN:
            if G.has_edge(nodei, nodej):
                l += ls_martix[nodei][nodej]
    return float(l / 2.0)


def calculate_LC(G1_KN, G2_KN):
    lc_12 = calculate_L(G1_KN, G2_KN)
    lc_1 = calculate_L(G1_KN, G1_KN)
    lc_2 = calculate_L(G2_KN, G2_KN)
    if lc_1 == 0 or lc_2 == 0:
        # 这种应该默认的不是吧重叠候选节点吧
        return u + 1
    lc = max(lc_12 / lc_1, lc_12 / lc_2)
    return lc


# 找到候选的重叠节点
def find_overlapping_candidates(G, u):
    overlapping_candidate_nodes = []
    for node in list(G.nodes):
        NB_i = list(nx.neighbors(G, node))
        if len(NB_i) == 0:
            continue
        node_KN_dict = calculate_node_KN(NB_i, node)
        if len(node_KN_dict) < 2:
            continue
        node_KNs = node_KN_dict.values()
        G1_KN = node_KNs[0]
        G2_KN = node_KNs[1]
        lc = calculate_LC(G1_KN, G2_KN)
        if lc <= u:
            overlapping_candidate_nodes.append(node)
    return overlapping_candidate_nodes


def start(param, run_windows_lfr=False):
    if not isinstance(param, AlgorithmParam):
        raise Exception("你想搞啥呢？？？？？")

    test_data = param.dataset
    need_show_image = param.need_show_image

    # 算法执行开始时间，统计这个算法运行花费时间
    start_time = time.time()

    global G, dist_martix, ls_martix
    global node_outgoing_weight_dict, node_knn_neighbors_dict
    global all_nodes_info_list
    global u, node_influence_dict
    global path

    # result 统一保存所有的中间结果
    result = MyResultInof()

    need_print_result = True
    # 如果是linux环境，则自动生成网络
    if run_platform == "linux":
        need_print_result = False
        generate_network(param, path)
        # 处理LFR数据
        G, true_overlapping_nodes, true_community_num = transfer_2_gml(path=path + "/")
        result.true_overlapping_nodes = true_overlapping_nodes
        result.true_community_num = true_community_num
    else:
        # 基本上是在window上测试使用算法是否正确的时候使用
        G.add_edges_from([(1, 2), (2, 3), (2, 4), (2, 5),
                          (2, 6), (2, 7),
                          (7, 8), (8, 9), (9, 10), (9, 11), (9, 12),
                          (9, 13), (9, 14), (9, 15), (9, 17), (9, 18), (9, 19), (9, 20),
                          (9, 16), (12, 13), (12, 11),
                          (12, 17), (12, 18), (12, 19), (12, 20), (12, 21), (20, 21)], weight=1.0)
        # dolphins的数据需要在网络图上加上1，也就是网络图上40，对应的真实的数据是39
        G = nx.read_gml(path + test_data, label="id")
        if run_windows_lfr:
            G, true_overlapping_nodes, true_community_num = transfer_2_gml(path=path)
            result.true_overlapping_nodes = true_overlapping_nodes
            result.true_community_num = true_community_num
    result.G = G

    # 默认边的权重为1.0
    for edge in G.edges:
        if G[edge[0]][edge[1]].get('weight', -1000) == -1000:
            G[edge[0]][edge[1]]['weight'] = 1.0

    # 初始化所有节点的outging_weight的值
    for node in G.nodes:
        outgoing_weight = calculate_node_outgoing_weight(node)
        node_outgoing_weight_dict[node] = outgoing_weight

    # 1) 初始化dist_martix，这一步是整个算法的基础，只有初始化dist_martix正确之后，后面的逻辑才走得通
    # ls_martix主要在second_step中使用到了，所以在这一步也初始化好
    dist_martix, ls_martix = init_dist_martix()
    print "init dist martix end......."
    # 初始化好每个节点的knn_neighbors，避免后面重复计算，提高效率
    knn = calculate_knn()
    for node in G.nodes:
        knn_neighbors = calculate_node_knn_neighbor(node, knn)
        node_knn_neighbors_dict[node] = knn_neighbors

    # 2) all_nodes_info_list 很重要，所有节点的信息统一放在这个list中
    all_nodes_info_list, node_node_r_dict = init_all_nodes_info(param.node_g_weight)
    print 'init all nodes info end......'

    # all_nodes_info_dict 便于后面从filter_node_list中通过node信息来更新到all_nodes_info_list上的信息
    all_nodes_info_dict = {node_info.node: node_info for node_info in all_nodes_info_list}

    # 按照node_r进行排序,因为论文的算法二中选择中心节点就是使用的过滤之后的节点进行筛选的
    filter_nodes_info_list, averge_node_r = filter_corredpond_nodes(all_nodes_info_list)

    # 按照节点的node_r进行排序，这里需要进行拟合
    filter_nodes_info_list = sorted(filter_nodes_info_list, key=lambda x: x.node_r)

    # 非核心逻辑，不用管
    ascending_nod_p_nodes, ascending_nod_r_nodes = \
        calculate_ascending_nodes(filter_nodes_info_list, all_nodes_info_list)

    result.ascending_nod_p_nodes = ascending_nod_p_nodes
    result.ascending_nod_r_nodes = ascending_nod_r_nodes

    # 2) 初始化所有没有被过滤的节点的d伽马
    init_filter_nodes_dr(filter_nodes_info_list)
    print 'init filter nodes end.......'

    # 非核心(不重要)
    need_show_data(all_nodes_info_list, filter_nodes_info_list, need_show_image)

    # 4) 选择中心节点的逻辑(重要)
    # filter_nodes_info_list_index 表示的是过滤的节点的list的下标之后的所有节点为中心节点
    filter_nodes_info_list_index = select_center(filter_nodes_info_list, averge_node_r)
    # print filter_nodes_info_list_index, len(filter_nodes_info_list)
    print "select center nodes end......"

    center_node_dict = init_center_node(filter_nodes_info_list_index, filter_nodes_info_list, all_nodes_info_dict)

    # 5) first_stpe, 将所有的非中心节点进行划分
    # 讲道理到了这一步之后，所有的节点都是已经划分了一个社区的，然后通过second_step()进行二次划分，将重叠节点找出来，并划分
    node_community_dict, ls_zero_nodes = first_step(center_node_dict)

    center_nodes = sorted(list(center_node_dict.keys()))
    result.center_nodes = center_nodes

    not_overlapping_node_community_dict = node_community_dict.copy()
    print "first step end......."

    result.ls_zero_nodes = ls_zero_nodes

    # 6) second_step, 将所有的可能是重叠节点的节点进行划分
    overlapping_candidates = find_overlapping_candidates(G, param.u)
    result.overlapping_candidates = overlapping_candidates

    not_enveloped_nodes = second_step(node_community_dict, param.c, param.enveloped_weight, overlapping_candidates)
    result.not_enveloped_nodes = not_enveloped_nodes
    result.node_community_dict = node_community_dict
    print 'second step end.......'
    # print overlapping_candidates
    # print len(result.true_overlapping_nodes), len(not_enveloped_nodes), len(set(result.true_overlapping_nodes) & set(not_enveloped_nodes))
    print len(result.true_overlapping_nodes), len(overlapping_candidates), len(
        set(result.true_overlapping_nodes) & set(overlapping_candidates))
    # print len(overlapping_candidates), len(not_enveloped_nodes), len(set(overlapping_candidates) & set(not_enveloped_nodes))

    # 7) 下面都是一些处理结果的逻辑，不是很核心
    # community_nodes_dict 每个社区对应的节点信息
    community_nodes_dict, not_overlapping_community_node_dict = \
        handle_result_to_txt(all_nodes_info_list, not_overlapping_node_community_dict)
    result.community_nodes_dict = community_nodes_dict
    result.not_overlapping_community_node_dict = not_overlapping_community_node_dict

    find_overlapping_nodes_dict, min_om, max_om = calculate_overlapping_nodes(node_community_dict)
    find_overlapping_nodes = list(find_overlapping_nodes_dict.keys())
    mapping_overlapping_nodes = overlapping_mapping_sum(find_overlapping_nodes,
                                                        result.true_overlapping_nodes)
    result.find_overlapping_nodes = find_overlapping_nodes
    result.mapping_overlapping_nodes = mapping_overlapping_nodes
    result.max_om = max_om
    result.min_om = min_om

    # 在linux上直接计算onmi的值，避免手动复制计算(麻烦)
    if run_platform == "linux":
        from my_evaluation import calculate_onmi
        onmi = calculate_onmi(path)
        result.onmi = onmi

    # 统计一下时间而已，不重要
    end_time = time.time()
    spend_seconds = end_time - start_time
    result.spend_seconds = spend_seconds

    # # 打印一些结果，供观察算法输出情况
    # print_result(result, need_print_result)

    return result, need_print_result


def need_update_path(new_makdir=None):
    global path
    if new_makdir is not None:
        # 更新path
        path = path + new_makdir
        if os.path.exists(path):
            shutil.rmtree(path)
        os.makedirs(path)
        print "generate mkdir {} ".format(path)


def run_linux_generate_picture(steps=5, summary_table="community_summary7", detail_table="community_detail7",
                               need_add_to_mysql=False):
    ###############################################
    # 如果想要在linux运行并自动生成图像，那么就需要控制下面的这些参数（最多控制三个变量）
    # 1）一般三个控制变量，第一个控制变量用于控制生成多张对比图像
    # 2）如果是两个变量的话，那么只会生成一张图像，第一个变量就是生成一张图像上的多条曲线，第二个变量就是图像的x轴
    # 3）如果想在linux的shell窗口运行的多个的话，需要每次修改不同的 new_makdir，
    # 因为需要生成lfr数据，如果公用一个目录，数据会混论
    # 4）迭代次数，一般默认取5，其实在这里如果将迭代的次数修改为更大的数据的话，并且在得到ONMI值得时候，多排除几个小的数据，结果会更好
    # 5）need_add_to_mysql 表示是否将结果存入mysql中
    ###############################################
    param_dict = {"test1": generate_muw_u_on, "test2": generate_n_muw_om,
                  "test3": generate_n_muw_on, "test4": generate_muw_n_om}
    # need_make_dir 主要是在linux运行多个程序的时候，隔离开每个程序的文件生成目录，使用docker运行的时候可以不用该参数
    new_makdir = "test3"  # 所以想要在linux上执行多个窗口，那么此处就需要附上值
    need_update_path(new_makdir)
    if new_makdir == "test4":
        steps = 3
    params, show_image_params = param_dict.get(new_makdir)()

    # 每一轮执行10个迭代
    y_trains_all = []
    i = 0
    for for_1 in params:
        y_tains = []
        for for_2 in for_1:
            y_train_i = []
            for param in for_2:
                print '-' * 30
                print "n={}, k={}, maxk={}, minc={}, maxc={}, mut={}, muw={}, on={}, " \
                      "om={}, c={}, node_g_weight={}".format(param.n, param.k,
                                                             param.maxk, param.minc, param.maxc, param.mut,
                                                             param.muw,
                                                             param.on, param.om, param.c, param.node_g_weight)
                step_results = []
                for i in range(0, steps):
                    result, _ = start(param)
                    step_results.append(result)
                    print i, result.onmi
                # 将每一轮结果处理，并存入数据库中，方便后续统计分析

                onmi = add_result_to_mysql(param, step_results, summary_table, detail_table, need_add_to_mysql)
                # onmi = random.random()
                y_train_i.append(onmi)
                print '-' * 30
            y_tains.append(y_train_i)
        show_image_params[i].y_trains = y_tains
        i += 1
        y_trains_all.append(y_tains)
        print "*" * 30
        # y_trains_all 表示需要描绘图像的数据
        for y_tains in y_trains_all:
            for x in y_tains:
                print x
            print "------------------------"
        print "*" * 30
    for show_image_param in show_image_params:
        show_result_image(show_image_param)

    # 删除临时文件夹
    if new_makdir is not None:
        shutil.rmtree(path)


if __name__ == '__main__':
    global path
    if run_platform == "linux":
        print "linux to run start......"
        steps = 5
        summary_table = "community_summary7"
        detail_table = "community_detail7"
        need_add_to_mysql = False
        run_linux_generate_picture(steps, summary_table, detail_table)
    else:
        param = AlgorithmParam()
        param.node_g_weight = 1.0
        param.enveloped_weight = 0.5
        param.dataset = "dolphins.gml"
        param.need_show_image = False
        # 如果需要在window平台下运行，lfr生成数据(由于linux平台生成并拷贝到windows下)，将该参数改为True
        run_windows_lfr = True

        result, need_print_result = start(param, run_windows_lfr)
        # 当然也可以直接在windows上跑，然后将结果存入数据库中，问题就是windows下不好生成lfr的网络数据
        # add_result_to_mysql(param, [result])
        # window下本机测试，直接打印相应的结果就好
        print_result(result, need_print_result)
