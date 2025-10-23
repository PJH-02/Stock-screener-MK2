import pandas as pd
from utils import setup_logger

logger = setup_logger('newness_analyzer')

class NewnessAnalyzer:
    """Analyzes N (Newness) criterion."""
    
    def check_n_criterion(self, ticker, ohlcv):
        """
        N - Newness: Current price ≥ 85% of 52-week high.
        
        Args:
            ticker: Stock ticker
            ohlcv: DataFrame with OHLCV data
        
        Returns: (pass: bool, details: dict)
        """
        if ohlcv is None or ohlcv.empty:
            return False, {'reason': 'No OHLCV data available'}
        
        try:
            # Get last 252 trading days (approximately 1 year)
            recent_data = ohlcv.tail(252)
            
            if len(recent_data) < 200:  # Need reasonable amount of data
                return False, {'reason': 'Insufficient price history'}
            
            # Calculate 52-week high
            high_52w = recent_data['고가'].max()
            current_price = ohlcv['종가'].iloc[-1]
            
            # Check if current price is at least 85% of 52-week high
            price_ratio = (current_price / high_52w) * 100
            passes = price_ratio >= 85
            
            return passes, {
                'current_price': current_price,
                '52w_high': high_52w,
                'price_ratio': round(price_ratio, 2)
            }
            
        except Exception as e:
            logger.debug(f"Error checking N criterion for {ticker}: {e}")
            return False, {'reason': str(e)}
