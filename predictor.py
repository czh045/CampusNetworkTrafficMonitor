import pymysql
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.multioutput import MultiOutputRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import joblib
import os
from datetime import datetime, timedelta
import random
import math
from pathlib import Path

from config import get_db_connection

class TrafficPredictor:
    def __init__(self):
        self.model = None
        self.models_dir = Path(__file__).resolve().parent / 'models'
        self.models_dir.mkdir(exist_ok=True)

    def get_db(self):
        return get_db_connection()

    def get_historical_data(self, hours=168):
        """获取历史数据，按小时聚合并返回时间序列"""
        conn = self.get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                DATE_FORMAT(timestamp, '%%Y-%%m-%%d %%H:00:00') as hour_time,
                AVG(in_rate) as avg_in,
                AVG(out_rate) as avg_out
            FROM realtime_traffic
            WHERE timestamp >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            GROUP BY DATE_FORMAT(timestamp, '%%Y-%%m-%%d %%H:00:00')
            ORDER BY hour_time
        """, (hours,))

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        min_rows = min(hours, 8)
        if len(rows) < min_rows:
            return None

        data = []
        for row in rows:
            dt = datetime.strptime(row[0], '%Y-%m-%d %H:00:00')
            avg_in = float(row[1]) if row[1] else 0
            avg_out = float(row[2]) if row[2] else 0
            data.append((dt, avg_in, avg_out))

        return data

    def train_model(self):
        """训练预测模型"""
        try:
            history = self.get_historical_data(168)

            if not history or len(history) < 8:
                print(f"数据不足（{len(history) if history else 0}小时），使用模拟模式")
                return self.use_mock_mode()

            X = []
            y = []
            lags = [1, 2, 3, 6, 12, 24]
            available_lags = [lag for lag in lags if lag < len(history)]
            if not available_lags:
                available_lags = [1]

            for i in range(max(available_lags), len(history)):
                dt, avg_in, avg_out = history[i]
                features = []
                for lag in available_lags:
                    past_in, past_out = history[i - lag][1], history[i - lag][2]
                    features.extend([past_in, past_out])
                features.append(math.sin(2 * math.pi * dt.hour / 24))
                features.append(math.cos(2 * math.pi * dt.hour / 24))
                features.append(math.sin(2 * math.pi * dt.weekday() / 7))
                features.append(math.cos(2 * math.pi * dt.weekday() / 7))
                if len(history) >= 24:
                    features.append(history[i - 24][1])
                    features.append(history[i - 24][2])
                else:
                    features.append(history[0][1])
                    features.append(history[0][2])
                if i >= 2:
                    features.append(history[i - 1][1] - history[i - 2][1])
                    features.append(history[i - 1][2] - history[i - 2][2])
                else:
                    features.extend([0, 0])
                X.append(features)
                y.append([avg_in, avg_out])

            if len(X) < 2:
                print(f"训练样本不足（{len(X)}条），使用模拟模式")
                return self.use_mock_mode()

            self.model = MultiOutputRegressor(GradientBoostingRegressor(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                random_state=42
            ))
            self.model.fit(np.array(X), np.array(y))

            joblib.dump(self.model, self.models_dir / 'traffic_predictor.pkl')
            print(f"模型训练完成，使用 {len(X)} 条训练样本")
            return True

        except Exception as e:
            print(f"模型训练失败: {e}")
            return self.use_mock_mode()

    def use_mock_mode(self):
        """使用模拟模式"""
        self.model = None
        print("使用模拟预测模式")
        return True

    def load_model(self):
        """加载模型"""
        model_path = self.models_dir / 'traffic_predictor.pkl'
        if model_path.exists():
            try:
                self.model = joblib.load(model_path)
                print("模型加载成功")
                return True
            except Exception as e:
                print(f"模型加载失败: {e}")
                self.model = None
                return False
        return False

    def get_model_metrics(self):
        """获取模型评估指标（加固版，永不报错）"""
        try:
            history = self.get_historical_data(168)

            if not history or len(history) < 8:
                return {
                    'mode': 'mock',
                    'trained': False,
                    'samples': len(history) if history else 0,
                    'rmse_in': None,
                    'rmse_out': None,
                    'mae_in': None,
                    'mae_out': None,
                    'r2_in': None,
                    'r2_out': None,
                    'message': f'当前历史数据{len(history) if history else 0}小时，至少需要8小时才能启用模型预测。'
                }

            if self.model is None:
                self.load_model()

            if self.model is None:
                return {
                    'mode': 'mock',
                    'trained': False,
                    'samples': len(history),
                    'rmse_in': None,
                    'rmse_out': None,
                    'mae_in': None,
                    'mae_out': None,
                    'r2_in': None,
                    'r2_out': None,
                    'message': '模型未加载，当前使用模拟预测。'
                }

            lags = [1, 2, 3, 6, 12, 24]
            available_lags = [lag for lag in lags if lag < len(history)]
            if not available_lags:
                available_lags = [1]

            X = []
            y = []
            for i in range(max(available_lags), len(history)):
                dt, avg_in, avg_out = history[i]
                features = []
                for lag in available_lags:
                    past_in, past_out = history[i - lag][1], history[i - lag][2]
                    features.extend([past_in, past_out])
                features.append(math.sin(2 * math.pi * dt.hour / 24))
                features.append(math.cos(2 * math.pi * dt.hour / 24))
                features.append(math.sin(2 * math.pi * dt.weekday() / 7))
                features.append(math.cos(2 * math.pi * dt.weekday() / 7))
                if len(history) >= 24:
                    features.append(history[i - 24][1])
                    features.append(history[i - 24][2])
                else:
                    features.append(history[0][1])
                    features.append(history[0][2])
                if i >= 2:
                    features.append(history[i - 1][1] - history[i - 2][1])
                    features.append(history[i - 1][2] - history[i - 2][2])
                else:
                    features.extend([0, 0])
                X.append(features)
                y.append([avg_in, avg_out])

            if len(X) == 0:
                return {
                    'mode': 'mock',
                    'trained': False,
                    'samples': len(history),
                    'message': '评估数据不足，使用模拟预测。'
                }

            y = np.array(y)
            preds = self.model.predict(np.array(X))

            rmse = np.sqrt(mean_squared_error(y, preds, multioutput='raw_values'))
            mae = mean_absolute_error(y, preds, multioutput='raw_values')
            r2 = r2_score(y, preds, multioutput='raw_values')

            return {
                'mode': 'model',
                'trained': True,
                'samples': len(X),
                'rmse_in': float(rmse[0]),
                'rmse_out': float(rmse[1]),
                'mae_in': float(mae[0]),
                'mae_out': float(mae[1]),
                'r2_in': float(r2[0]),
                'r2_out': float(r2[1])
            }

        except Exception as e:
            print(f"获取指标失败: {e}")
            return {
                'mode': 'mock',
                'trained': False,
                'samples': 0,
                'message': f'评估出错，使用模拟预测。'
            }

    def predict_next(self, hours=24):
        """预测未来流量（加固版，永不报错）"""
        try:
            history = self.get_historical_data(72)

            if not history or len(history) < 4:
                return self.mock_predict(hours)

            if self.model is None:
                self.load_model()

            lags = [1, 2, 3, 6, 12, 24]
            last_values = [(avg_in, avg_out) for _, avg_in, avg_out in history[-min(36, len(history)):]]
            available_lags = [lag for lag in lags if lag <= len(last_values)]
            if not available_lags:
                available_lags = [1]

            predictions = []
            in_predictions = []
            out_predictions = []
            now = datetime.now()

            for i in range(hours):
                future_dt = now + timedelta(hours=i + 1)
                features = []

                for lag in available_lags:
                    if lag <= len(last_values):
                        past_in, past_out = last_values[-lag]
                    else:
                        past_in, past_out = last_values[0]
                    features.extend([past_in, past_out])

                features.append(math.sin(2 * math.pi * future_dt.hour / 24))
                features.append(math.cos(2 * math.pi * future_dt.hour / 24))
                features.append(math.sin(2 * math.pi * future_dt.weekday() / 7))
                features.append(math.cos(2 * math.pi * future_dt.weekday() / 7))

                if len(last_values) >= 24:
                    features.append(last_values[-24][0])
                    features.append(last_values[-24][1])
                else:
                    features.append(last_values[0][0])
                    features.append(last_values[0][1])

                if len(last_values) >= 2:
                    features.append(last_values[-1][0] - last_values[-2][0])
                    features.append(last_values[-1][1] - last_values[-2][1])
                else:
                    features.extend([0, 0])

                if self.model is not None:
                    try:
                        pred = self.model.predict([features])[0]
                        pred_in, pred_out = pred[0], pred[1]
                    except:
                        pred_in, pred_out = self.periodic_predict(last_values, i)
                else:
                    pred_in, pred_out = self.periodic_predict(last_values, i)

                pred_in = max(5, min(150, pred_in))
                pred_out = max(5, min(150, pred_out))
                predictions.append(round(pred_in + pred_out, 2))
                in_predictions.append(round(pred_in, 2))
                out_predictions.append(round(pred_out, 2))
                last_values.append((pred_in, pred_out))
                if len(last_values) > 48:
                    last_values.pop(0)

            return {
                'mode': 'model' if self.model is not None else 'fallback',
                'trained': self.model is not None,
                'hours': [(now + timedelta(hours=i+1)).strftime('%H:00') for i in range(hours)],
                'predictions': predictions,
                'in_predictions': in_predictions,
                'out_predictions': out_predictions,
                'peak': max(predictions),
                'peak_hour': (now + timedelta(hours=predictions.index(max(predictions))+1)).strftime('%H:00'),
                'avg': round(sum(predictions) / len(predictions), 2)
            }

        except Exception as e:
            print(f"预测失败: {e}")
            return self.mock_predict(hours)

    def periodic_predict(self, last_values, step):
        """基于周期规律的预测（模拟真实流量模式）"""
        now = datetime.now()
        pred_hour = (now.hour + step + 1) % 24

        if 8 <= pred_hour <= 22:
            base = 80 + math.sin(pred_hour * math.pi / 14) * 30
        elif 22 <= pred_hour <= 24 or 0 <= pred_hour <= 2:
            base = 45 + math.sin(pred_hour * math.pi / 12) * 15
        else:
            base = 20 + math.sin(pred_hour * math.pi / 8) * 10

        rng = random.Random(f'{now.date()}-{pred_hour}-{step}')
        noise_in = rng.uniform(-8, 8)
        noise_out = rng.uniform(-6, 6)

        if len(last_values) >= 2:
            trend_in = (last_values[-1][0] - last_values[-2][0]) * 0.25
            trend_out = (last_values[-1][1] - last_values[-2][1]) * 0.25
        else:
            trend_in = trend_out = 0

        pred_in = max(5, min(150, base * 0.6 + noise_in + trend_in))
        pred_out = max(5, min(150, base * 0.4 + noise_out + trend_out))
        return pred_in, pred_out

    def mock_predict(self, hours=24):
        """Fallback prediction with repeatable values for the current hour."""
        predictions = []
        in_predictions = []
        out_predictions = []
        now = datetime.now()

        for i in range(hours):
            pred_hour = (now.hour + i + 1) % 24

            if 8 <= pred_hour <= 22:
                base = 80 + math.sin(pred_hour * math.pi / 14) * 30
            elif 22 <= pred_hour <= 24 or 0 <= pred_hour <= 2:
                base = 45
            else:
                base = 20

            rng = random.Random(f'{now.date()}-{pred_hour}-{i}')
            noise_in = rng.uniform(-8, 8)
            noise_out = rng.uniform(-6, 6)
            in_pred = max(5, min(150, base * 0.6 + noise_in))
            out_pred = max(5, min(150, base * 0.4 + noise_out))
            in_predictions.append(round(in_pred, 2))
            out_predictions.append(round(out_pred, 2))
            predictions.append(round(in_pred + out_pred, 2))

        return {
            'mode': 'fallback',
            'trained': False,
            'hours': [(now + timedelta(hours=i+1)).strftime('%H:00') for i in range(hours)],
            'predictions': predictions,
            'in_predictions': in_predictions,
            'out_predictions': out_predictions,
            'peak': max(predictions),
            'peak_hour': (now + timedelta(hours=predictions.index(max(predictions))+1)).strftime('%H:00'),
            'avg': round(sum(predictions) / len(predictions), 2)
        }

    def get_anomaly_score(self, current_value):
        """计算异常分数"""
        history = self.get_historical_data(24)
        if not history or len(history) < 10:
            return 0
        values = [avg_in + avg_out for _, avg_in, avg_out in history]
        mean = sum(values) / len(values)
        std = (sum((x - mean) ** 2 for x in values) / len(values)) ** 0.5

        if std == 0:
            return 0

        z_score = abs(current_value - mean) / std
        return min(100, z_score * 20)


if __name__ == '__main__':
    p = TrafficPredictor()
    p.train_model()

    result = p.predict_next(12)
    if result:
        print("预测结果:")
        print(f"  峰值: {result['peak']} Mbps")
        print(f"  平均: {result['avg']} Mbps")
        print(f"  未来12小时: {result['predictions'][:12]}")
