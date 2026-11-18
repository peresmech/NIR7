def clean_data(df):
    df = df.dropna(subset=['discharge', 'level'])
    
    # убираем явные выбросы (очень грубо пока)
    df = df[(df['discharge'] > 0) & (df['level'] > 0)]
    
    return df