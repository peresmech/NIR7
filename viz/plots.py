import matplotlib.pyplot as plt

def plot_timeseries(df):
    fig, ax1 = plt.subplots()

    ax1.plot(df['date'], df['discharge'], label='Q')
    ax1.set_ylabel('Discharge')

    ax2 = ax1.twinx()
    ax2.plot(df['date'], df['level'], color='orange', label='η')
    ax2.set_ylabel('Level')

    plt.title("Time Series")
    plt.show()


def plot_hysteresis(Q, eta):
    plt.figure()
    plt.plot(Q, eta, '-o')
    plt.xlabel('Q')
    plt.ylabel('η')
    plt.title('Hysteresis η(Q)')
    plt.grid()
    plt.show()