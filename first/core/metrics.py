import numpy as np

def delta_eta(eta_up, eta_down):
    """
    Простейшая оценка Δη
    """
    return np.abs(np.mean(eta_up) - np.mean(eta_down))


def loop_area(Q, eta):
    """
    Площадь петли (через формулу Грина)
    """
    return 0.5 * np.abs(
        np.dot(Q, np.roll(eta, 1)) - np.dot(eta, np.roll(Q, 1))
    )