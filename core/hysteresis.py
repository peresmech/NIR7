import numpy as np

def build_hysteresis(df):
    """
    Возвращает массивы Q и η
    """
    return df['discharge'].values, df['level'].values


def split_branches(Q, eta):
    """
    Разделение на восходящую и нисходящую ветви
    """
    peak_idx = Q.argmax()
    
    Q_up = Q[:peak_idx]
    eta_up = eta[:peak_idx]
    
    Q_down = Q[peak_idx:]
    eta_down = eta[peak_idx:]
    
    return (Q_up, eta_up), (Q_down, eta_down)