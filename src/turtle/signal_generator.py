import pandas as pd
from utils import setup_logger

logger = setup_logger('turtle_signal')

class TurtleSignalGenerator:
    """Generates Turtle Trading signals (M - Market Direction)."""
    
    def generate_signals(self, ticker, ohlcv):
        """
        Generate Turtle Trading signals for a stock.
        
        Buy Signals:
        - S1_Buy: Price breaks 20-day high
        - S2_Buy: Price breaks 55-day high
        
        Exit Signals:
        - S1_Exit: Price breaks 10-day low
        - S2_Exit: Price breaks 20-day low
        
        Args:
            ticker: Stock ticker
            ohlcv: DataFrame with OHLCV data
        
        Returns: list of signal strings (can be multiple)
        """
        if ohlcv is None or ohlcv.empty:
            return []
        
        try:
            if len(ohlcv) < 55:
                return []
            
            signals = []
            
            # Get current and historical prices
            current_price = ohlcv['종가'].iloc[-1]
            current_high = ohlcv['고가'].iloc[-1]
            current_low = ohlcv['저가'].iloc[-1]
            
            # Calculate breakout levels
            high_20d = ohlcv['고가'].iloc[-21:-1].max()  # Exclude current day
            high_55d = ohlcv['고가'].iloc[-56:-1].max()  # Exclude current day
            low_10d = ohlcv['저가'].iloc[-11:-1].min()   # Exclude current day
            low_20d = ohlcv['저가'].iloc[-21:-1].min()   # Exclude current day
            
            # Check for buy signals (price breaks above resistance)
            if current_high > high_20d:
                signals.append('S1_Buy')
            
            if current_high > high_55d:
                signals.append('S2_Buy')
            
            # Check for exit signals (price breaks below support)
            if current_low < low_10d:
                signals.append('S1_Exit')
            
            if current_low < low_20d:
                signals.append('S2_Exit')
            
            return signals
            
        except Exception as e:
            logger.debug(f"Error generating turtle signals for {ticker}: {e}")
            return []
    
    def get_signal_details(self, ticker, ohlcv):
        """
        Get detailed information about current Turtle Trading levels.
        
        Args:
            ticker: Stock ticker
            ohlcv: DataFrame with OHLCV data
        
        Returns: dict with signal details
        """
        if ohlcv is None or ohlcv.empty or len(ohlcv) < 55:
            return {}
        
        try:
            current_price = ohlcv['종가'].iloc[-1]
            
            high_20d = ohlcv['고가'].iloc[-21:-1].max()
            high_55d = ohlcv['고가'].iloc[-56:-1].max()
            low_10d = ohlcv['저가'].iloc[-11:-1].min()
            low_20d = ohlcv['저가'].iloc[-21:-1].min()
            
            return {
                'current_price': current_price,
                'high_20d': high_20d,
                'high_55d': high_55d,
                'low_10d': low_10d,
                'low_20d': low_20d,
                'distance_to_s1_buy': round(((high_20d - current_price) / current_price) * 100, 2),
                'distance_to_s2_buy': round(((high_55d - current_price) / current_price) * 100, 2),
                'distance_to_s1_exit': round(((current_price - low_10d) / current_price) * 100, 2),
                'distance_to_s2_exit': round(((current_price - low_20d) / current_price) * 100, 2)
            }
        except Exception as e:
            logger.debug(f"Error getting signal details for {ticker}: {e}")
            return {}
