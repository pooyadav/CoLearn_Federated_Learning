# Proof-of-Concept of feature generator after argus processing.
# This script was created with the intent of generate from normal pcap flows the same dataset of Bot-IoT. 
# So, the model created could be used in a real environment.
# The biggest difference between these features and the one generated by Bot-IoT dataset, is that the latter
# uses a sliding window of 100 connections (not available in our case)

# To geneate a compatible csv file to be processed you must use argus:
# For example
# argus -r file.pcap -w file.argus # conversion from pcap file to argus file
# ra -L0 -c , -s +ltime +min +max +seq +mean +stddev +sum +spkts +spkts +sbytes +dbytes +rate +srate +drate +dur -r file.argus > file.csv

# Import libraries
import argparse
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt

# Arguments
parser = argparse.ArgumentParser(description="Run features generator")
parser.add_argument(
    "--input", "-i", type=str, required=True, help="csv input file"
)
parser.add_argument("--output", "-o", type=str, required=True, help="csv output file")

parser.add_argument("--extract", "-e", type=bool, nargs='?',
                        const=True, default=False,
                        help="Extract 10 useful features")

def generate(input_file, output_file, extract):

    df = pd.read_csv(input_file)

    # Map the lowering function to all column names: This is needed only for our generated dataset
    mapping = {
        'srcaddr': 'saddr', 
        'dstaddr' : 'daddr', 
        'srcbytes': 'sbytes', 
        'dstbytes': 'dbytes', 
        'srcpkts': 'spkts', 
        'dstpkts': 'dpkts', 
        'srcrate': 'srate', 
        'dstrate': 'drate',
        'totpkts': 'pkts',
        'totbytes': 'bytes'
        }
    df.columns = map(str.lower, df.columns)
    df = df.rename(columns=mapping, errors="raise")

    # State number dict
    state_numbers = {
        'RST': 1,
        'CON': 2,
        'REQ': 3,
        'INT': 4,
        'URP': 5,
        'FIN': 6,
    }
    df['state_number'] = df['state'].map(state_numbers)
    index_nan_state_number = df.loc[pd.isna(df['state_number'])].index
    df.loc[index_nan_state_number, 'state_number'] = -1
    df = df.astype({'state_number':'int64'})
    # Due to the existence of certain protocols (ARP), source and destination
    # port number values were missing (not applicable), as such, these values
    # were set to -1,
    index_nan_sport = df.loc[pd.isna(df['sport'])].index
    index_nan_dport = df.loc[pd.isna(df['dport'])].index

    df.loc[index_nan_sport,'sport'] = -1
    df.loc[index_nan_dport,'dport'] = -1


    # New Feature generation: In this case we don't consider a sliding window of 100 connections (no sliding window)
    # Total number of bytes per source IP
    # What does it mean? All the bytes send in each transaction by that IP (in a sliding window of 100)
    df['TnBPSrcIP'] = df.groupby('saddr')['sbytes'].transform('sum')

    # Total number of bytes per destination IP
    df['TnBPDstIP'] = df.groupby('daddr')['dbytes'].transform('sum')

    # Total number of packets per source IP
    df["TnP_PSrcIP"] = df.groupby('saddr')['spkts'].transform('sum')

    # Total number of packets per destination IP
    df["TnP_PDstIP"] = df.groupby('daddr')['dpkts'].transform('sum')

    # Total number of packets per protocol
    df["TnP_PerProto"] = df.groupby('proto')['pkts'].transform('sum')

    # Total number of packets per dport
    df["TnP_PerDport"] = df.groupby('dport')['pkts'].transform('sum')

    # Average rate per protocol per source IP, calculated by pkts/dur
    df["AR_P_Proto_P_SrcIP"] = df.groupby(['saddr', 'proto'])['pkts'].transform('sum') / df.groupby(['saddr', 'proto'])['dur'].transform('sum') # verify these values

    # Average rate per protocol per destination IP, calculated by pkts/dur
    df["AR_P_Proto_P_DstIP"] = df.groupby(['daddr', 'proto'])['pkts'].transform('sum') / df.groupby(['daddr', 'proto'])['dur'].transform('sum') # verify these values

    # Average rate per protocol per sport
    df['AR_P_Proto_P_Sport'] = df.groupby(['proto','sport'])['pkts'].transform('sum') / df.groupby(['proto', 'sport'])['dur'].transform('sum') # verify these values

    # Average rate per protocol per dport
    df['AR_P_Proto_P_Dport'] = df.groupby(['proto','dport'])['pkts'].transform('sum') / df.groupby(['proto', 'dport'])['dur'].transform('sum') # verify these values

    # Number of inbound connections per source IP  -> For TCP connections this is REQ indicating that a connection is being requested, CON connected, EST as well.
    number_req = df[df['state'] == 'REQ'].groupby(['saddr'])['state'].count()
    number_con = df[df['state'] == 'CON'].groupby(['saddr'])['state'].count()
    number_est = df[df['state'] == 'EST'].groupby(['saddr'])['state'].count()

    df['N_IN_Conn_P_SrcIP'] = 0
    for ip in number_req.keys():
        df.loc[df['saddr'].isin([ip]), 'N_IN_Conn_P_SrcIP'] += number_req.get(ip)

    for ip in number_con.keys():
        df.loc[df['saddr'].isin([ip]), 'N_IN_Conn_P_SrcIP'] += number_con.get(ip)

    for ip in number_est.keys():
        df.loc[df['saddr'].isin([ip]), 'N_IN_Conn_P_SrcIP'] += number_est.get(ip)

    # Number of inbound connections per destination IP  -> For TCP connections this is REQ indicating that a connection is being requested, CON connected, EST as well.
    number_req = df[df['state'] == 'REQ'].groupby(['daddr'])['state'].count()
    number_con = df[df['state'] == 'CON'].groupby(['daddr'])['state'].count()
    number_est = df[df['state'] == 'EST'].groupby(['daddr'])['state'].count()

    df['N_IN_Conn_P_DstIP'] = 0
    for ip in number_req.keys():
        df.loc[df['daddr'].isin([ip]), 'N_IN_Conn_P_DstIP'] += number_req.get(ip)

    for ip in number_con.keys():
        df.loc[df['daddr'].isin([ip]), 'N_IN_Conn_P_DstIP'] += number_con.get(ip)

    for ip in number_est.keys():
        df.loc[df['daddr'].isin([ip]), 'N_IN_Conn_P_DstIP'] += number_est.get(ip)




    # Numbers of packets grouped by state of flows and protocols per destination IP
    df['Pkts_P_State_P_Protocol_P_DestIP'] = df.groupby(['state','proto', 'daddr'])['pkts'].transform('sum')

    # Numbers of packets grouped by state of flows and protocols per source IP
    df['Pkts_P_State_P_Protocol_P_SrcIP'] = df.groupby(['state','proto', 'saddr'])['pkts'].transform('sum')
    
    if extract:
        df = df[['seq', 'stddev', 'N_IN_Conn_P_SrcIP','min', 'state_number', 'mean', 'N_IN_Conn_P_DstIP', 'drate', 'srate', 'max']]
    df.to_csv(output_file, float_format='%.3f')


def main(args):
    generate(args.input, args.output, args.extract)

if __name__ == "__main__":

    args = parser.parse_args()
    main(args)



